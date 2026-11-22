"""Execute the production connection check with a recording MDB getter."""
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from test_model_validation_native_contract import extract_method


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge/e3d21"


@pytest.mark.skipif(not shutil.which("dotnet"), reason="Managed connection harness requires dotnet")
def test_connection_observation_never_reads_database_after_a_fault_latch():
    source = (BRIDGE / "E3d21Context.cs").read_text(encoding="utf-8-sig")
    helpers = "\n\n".join(extract_method(source, name) for name in ("IsConnected", "CurrentMdb"))
    harness = r'''
using System;
using Aveva.Core.Database;
namespace Aveva.Core.Database {
    internal sealed class MDB {
        internal static int Reads;
        internal static Func<MDB> Getter;
        internal static MDB CurrentMDB { get { Reads++; return Getter(); } }
        public static bool operator ==(MDB a, MDB b) { throw new Exception("Forbidden SDK equality"); }
        public static bool operator !=(MDB a, MDB b) { throw new Exception("Forbidden SDK inequality"); }
        public override bool Equals(object value) { return ReferenceEquals(this, value); }
        public override int GetHashCode() { return base.GetHashCode(); }
    }
}
namespace E3d21AiBridge {
    internal sealed class E3d21Context {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        private static bool quarantine;
        private bool IsGeneratedCsgQuarantined() { return quarantine; }
        private static void ThrowIfGeneratedCsgQuarantined() {
            if (quarantine) throw new InvalidOperationException("Quarantined");
        }
        // PRODUCTION_HELPERS
        private static int checks;
        private static void Check(bool value, string label) {
            checks++; if (!value) throw new Exception(label);
        }
        private static void Reset() { quarantine = false; MDB.Reads = 0; MDB.Getter = () => new MDB(); }
        private void Latch() { nativeFaultState.Observe("Unhandled Database error 538 from connection_getter", null); }
        public static void Main() {
            Reset();
            var healthy = new E3d21Context();
            Check(healthy.IsConnected(), "Readable MDB is connected");
            Check(MDB.Reads == 1, "Exactly one MDB getter");
            MDB.Getter = () => null;
            Check(!healthy.IsConnected(), "Null MDB is disconnected without overloaded equality");
            MDB.Getter = () => { throw new InvalidOperationException("Ordinary unavailable MDB"); };
            Check(!healthy.IsConnected(), "Ordinary getter failure is disconnected");

            Reset();
            var faulted = new E3d21Context(); faulted.Latch();
            MDB.Getter = () => { throw new Exception("SDK getter after preexisting fault"); };
            Check(!faulted.IsConnected(), "Preexisting fault is disconnected");
            Check(MDB.Reads == 0, "No SDK getter after preexisting fault");
            Check(!faulted.IsConnected() && MDB.Reads == 0, "Latched fault blocks replay");

            foreach (bool throws in new[] { false, true }) {
                Reset(); var context = new E3d21Context();
                MDB.Getter = () => {
                    context.Latch();
                    if (throws) throw new InvalidOperationException("Ordinary exception accompanying native fault");
                    return new MDB();
                };
                Check(!context.IsConnected(), "Fault during getter prevents connected=true");
                Check(MDB.Reads == 1, "Fault case has only its triggering getter");
                Check(!context.IsConnected() && MDB.Reads == 1, "Fault callback forbids replay");
            }

            Reset(); quarantine = true;
            Check(!new E3d21Context().IsConnected(), "Quarantine is disconnected");
            Check(MDB.Reads == 0, "Quarantine avoids SDK");
            Console.WriteLine("Production connection fault-latch checks passed: " + checks);
        }
    }
}
'''.replace("// PRODUCTION_HELPERS", helpers)
    with tempfile.TemporaryDirectory(prefix="connection-fault-latch-") as temporary:
        target = Path(temporary)
        (target / "Harness.cs").write_text(harness, encoding="utf-8")
        shutil.copyfile(BRIDGE / "NativeDatabaseFaultState.cs", target / "NativeDatabaseFaultState.cs")
        (target / "Offline.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
            '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
        options = dict(cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
        built = subprocess.run(["dotnet", "build", "Offline.csproj", "-c", "Release", "--nologo", "-warnaserror"],
                               timeout=90, **options)
        assert built.returncode == 0, built.stdout + built.stderr
        ran = subprocess.run([str(target / "bin/Release/net48/Offline.exe")], timeout=30, **options)
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert "Production connection fault-latch checks passed: 15" in ran.stdout
        print(ran.stdout.strip())

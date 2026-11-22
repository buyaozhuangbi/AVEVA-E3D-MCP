"""Execute the production descriptor route without loading the E3D SDK."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_model_validation_native_contract import block_end


ROOT = Path(__file__).resolve().parents[1]


class GeneratedCsgHttpContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "Offline route harness requires dotnet")
    def test_real_route_validates_budgets_duplicates_and_unknown_fields(self):
        source = (ROOT / "bridge/e3d21/E3d21HttpAdapter.cs").read_text(encoding="utf-8-sig")
        marker = 'else if (method == "GET" && path == "/api/v1/element/generated-csg")'
        opening = source.index("{", source.index(marker) + len(marker))
        route = source[opening + 1:block_end(source, opening) - 1]
        harness = r'''
using System;
using System.Collections.Specialized;
using System.Linq;
class Inner { public NameValueCollection QueryString; }
class Carrier { public Inner Request; }
class Context {
    public int Calls, Trees, Items; public string Ref;
    public object GetGeneratedCsg(string r, int t, int i) { Calls++; Ref=r; Trees=t; Items=i; return this; }
}
class Harness {
    static int checks;
    static void Check(bool value) { checks++; if(!value) throw new Exception("Check " + checks); }
    static object Invoke(Carrier request, Context context) {
        object response = null; string sessionId = "SESSION";
        // ROUTE
        return response;
    }
    static void Reject(NameValueCollection query) {
        var ctx = new Context(); bool rejected=false;
        try { Invoke(new Carrier { Request=new Inner { QueryString=query } }, ctx); }
        catch(ArgumentException) { rejected=true; }
        Check(rejected && ctx.Calls==0);
    }
    static void Main() {
        var query=new NameValueCollection { {"refno", "=1/2"} };
        var ctx=new Context();
        Check(Invoke(new Carrier { Request=new Inner { QueryString=query } }, ctx)!=null);
        Check(ctx.Calls==1 && ctx.Ref=="=1/2" && ctx.Trees==8 && ctx.Items==64);
        query.Add("max_trees", "16"); query.Add("max_items", "256"); ctx=new Context();
        Invoke(new Carrier { Request=new Inner { QueryString=query } }, ctx);
        Check(ctx.Calls==1 && ctx.Trees==16 && ctx.Items==256);
        foreach(string key in new[]{"refno", "max_trees", "max_items"}) {
            var dup=new NameValueCollection { {"refno", "=1/2"} };
            if(key!="refno") dup.Add(key,"1");
            dup.Add(key,"2"); Reject(dup);
        }
        Reject(new NameValueCollection { {"refno","=1/2"}, {"unknown","1"} });
        foreach(string key in new[]{"max_trees", "max_items"})
            foreach(string value in new[]{"0","-1","257","1.0","true"," 2","+2","2147483648",""})
                Reject(new NameValueCollection { {"refno","=1/2"}, {key,value} });
        Reject(new NameValueCollection { {"max_trees","17"} });
        Console.WriteLine("Generated CSG HTTP checks passed: " + checks);
    }
}
'''.replace("// ROUTE", route)
        with tempfile.TemporaryDirectory(prefix="generated-csg-http-") as directory:
            path = Path(directory)
            (path / "Harness.cs").write_text(harness, encoding="utf-8")
            (path / "Harness.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework>'
                '<OutputType>Exe</OutputType><LangVersion>latest</LangVersion></PropertyGroup></Project>', encoding="utf-8")
            build = subprocess.run(["dotnet", "build", "Harness.csproj", "--nologo"], cwd=path,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(path / "bin/Debug/net48/Harness.exe")], cwd=path,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("Generated CSG HTTP checks passed: 26", run.stdout)


if __name__ == "__main__":
    unittest.main()

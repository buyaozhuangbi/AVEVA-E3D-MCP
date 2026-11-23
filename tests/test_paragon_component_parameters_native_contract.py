"""Run the guarded PARAM array writer with recording fakes; AVEVA is compile-only."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import pytest
from test_model_validation_native_contract import extract_method

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'bridge/e3d21'
SDK = Path(os.environ.get('E3D21_ROOT', r'C:\Program Files (x86)\AVEVA\Everything3D2.10'))
SOURCES = ('ParagonComponentParameters.cs', 'E3d21ParagonComponentParameters.cs', 'ParagonComponentCopy.cs', 'ParagonComponentCopyJournal.cs', 'E3d21ParagonComponentCopy.cs',
           'ParagonComponentTemplate.cs', 'E3d21ParagonComponentTemplate.cs', 'PmlScriptInput.cs', 'NativeDatabaseFaultState.cs')
sys.path.insert(0, str(ROOT/'src'))

@pytest.fixture(scope='module')
def component_parameters_receipts():
    if not shutil.which('dotnet') or not (SDK/'Newtonsoft.Json.dll').is_file():
        pytest.skip('Requires offline dotnet and JSON library')
    helpers = (BRIDGE/'E3d21Context.cs').read_text(encoding='utf-8-sig')
    source = (ROOT/'tests/e3d21/ParagonComponentParametersHarness.cs').read_text(encoding='utf-8-sig')
    source = source.replace('// PRODUCTION_NATIVE_HELPERS', '\n\n'.join(extract_method(helpers,name) for name in (
        'NativeRead','NativeWrite','ThrowIfNativeFaulted','OnUi','TryBeginCommand','EndCommand','IsCommandActive','BridgeOperationBusy')))
    with tempfile.TemporaryDirectory(prefix='paragon-component-parameters-') as temporary:
        target = Path(temporary)
        (target/'Harness.cs').write_text(source, encoding='utf-8')
        for name in SOURCES: shutil.copyfile(BRIDGE/name,target/name)
        shutil.copyfile(SDK/'Newtonsoft.Json.dll', target/'Newtonsoft.Json.dll')
        (target/'Offline.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net48</TargetFramework><LangVersion>latest</LangVersion><OutputType>Exe</OutputType></PropertyGroup><ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>Newtonsoft.Json.dll</HintPath></Reference></ItemGroup></Project>', encoding='utf-8')
        options = dict(cwd=target,capture_output=True,text=True,encoding='utf-8',errors='replace')
        built = subprocess.run(['dotnet','build','Offline.csproj','-c','Release','--nologo'],timeout=90,**options)
        assert built.returncode == 0, built.stdout+built.stderr
        run = subprocess.run([str(target/'bin/Release/net48/Offline.exe')],timeout=150,**options)
        output='\n'.join(line for line in run.stdout.splitlines() if not line.startswith('Component parameters receipt: '))
        assert run.returncode==0, output+run.stderr
        assert 'Component parameters production checks passed:' in output
        matrix=re.search(r'Component parameters native fault matrix: (\d+) SDK read/write positions / (\d+) return-or-throw cases',output)
        assert matrix and int(matrix[1])>500 and int(matrix[2])==int(matrix[1])*2
        print(output)
        return [json.loads(line.removeprefix('Component parameters receipt: ')) for line in run.stdout.splitlines() if line.startswith('Component parameters receipt: ')]

def test_component_parameters_native_contract(component_parameters_receipts):
    assert len(component_parameters_receipts)>=40
    results=[row['reply']['result'] for row in component_parameters_receipts]
    assert {value['status'] for value in results}>={'prepared','blocked','updated','unchanged','failed','rejected','duplicate'}
    for row in component_parameters_receipts:
        result=row['reply']['result']
        assert result['operation']=='paragon_component_parameters_set'
        assert len(result['progress'])==11
        assert result['request_replayed'] is False and result['progress']['retry_safe'] is False
        if result['status'] in {'updated','unchanged'}:
            assert result['progress']['known_target_refno']=='=2/1'
            assert result['progress']['known_target_refno_verified'] is True
            assert result['progress']['parameters_equal_request'] is True
            assert result['after']['setable'] is None
            assert result['before']['parameter_bits'] is not None
            assert result['after']['parameter_bits']==result['request']['parameter_bits']


def test_native_receipts_satisfy_component_parameters_python_contract(component_parameters_receipts):
    from aveva_e3d_mcp import paragon_component_parameters as consumer
    validator=consumer._validate
    validated_count=0
    for index,row in enumerate(component_parameters_receipts,1):
        if row['reply']['ok'] is not True:
            continue
        original=json.dumps(row,sort_keys=True)
        native={key:row['reply'][key] for key in ('ok','version','session_id','bridge_build','result')}
        try: validator(native,row['payload'])
        except Exception as error: pytest.fail(f'Production component parameters receipt {index} ({row["reply"]["result"]["status"]}): {error}')
        assert json.dumps(row,sort_keys=True)==original
        validated_count+=1
    assert validated_count>=20
    print(f'Validated {validated_count} unchanged successful HTTP component parameters native receipts through Python.')
    statuses=0
    for index,row in enumerate(component_parameters_receipts,1):
        result=row['reply']['result']
        if result['journal'] is None:
            continue
        envelope={key:row['reply'][key] for key in ('version','session_id','bridge_build')}
        envelope.update(ok=True,result=result['journal'])
        try: consumer._validate_status(envelope,{'request_id':result['request_id'],'expected_session_id':row['reply']['session_id']})
        except Exception as error: pytest.fail(f'Production component-parameters journal receipt {index}: {error}')
        statuses+=1
    assert statuses>=15
    print(f'Validated {statuses} managed native component-parameters journals, including stored faults/failed/rejected/duplicate evidence.')

@pytest.mark.skipif(not shutil.which('dotnet') or not (SDK/'Aveva.Core.Database.dll').is_file(),reason='Requires real SDK for offline compile-only check')
def test_component_parameters_compiles_against_installed_sdk_without_execution(tmp_path):
    for name in SOURCES: shutil.copyfile(BRIDGE/name,tmp_path/name)
    (tmp_path/'CompileOnly.cs').write_text('''using System; namespace E3d21AiBridge; public sealed partial class E3d21Context {
        private readonly NativeDatabaseFaultState nativeFaultState = new NativeDatabaseFaultState();
        [ThreadStatic] private static NativeDatabaseFaultState currentNativeFaultState;
        private bool IsCommandActive() => throw new NotImplementedException();
        private bool TryBeginCommand(string name) => throw new NotImplementedException();
        private void EndCommand() => throw new NotImplementedException();
        private static T NativeRead<T>(Func<T> f) => throw new NotImplementedException();
        private static void NativeWrite(Action f) => throw new NotImplementedException();
        private static void ThrowIfNativeFaulted() => throw new NotImplementedException();
        private T OnUi<T>(Func<T> f, bool allowNativeFault = false) => throw new NotImplementedException();
        private object CurrentElement() => throw new NotImplementedException();
    }''',encoding='utf-8')
    project=ET.Element('Project',Sdk='Microsoft.NET.Sdk'); properties=ET.SubElement(project,'PropertyGroup')
    ET.SubElement(properties,'TargetFramework').text='net48'; ET.SubElement(properties,'LangVersion').text='latest'
    refs=ET.SubElement(project,'ItemGroup')
    for name in ('Aveva.Core.Database','Aveva.Core.Utilities','Aveva.Core.Geometry','Newtonsoft.Json'):
        ref=ET.SubElement(refs,'Reference',Include=name); ET.SubElement(ref,'HintPath').text=str(SDK/(name+'.dll')); ET.SubElement(ref,'Private').text='false'
    ET.ElementTree(project).write(tmp_path/'CompileOnly.csproj',encoding='unicode')
    built=subprocess.run(['dotnet','build','CompileOnly.csproj','-c','Release','--nologo'],cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    assert built.returncode==0,built.stdout+built.stderr

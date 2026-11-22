#if CAMERA_READBACK_TEST_HARNESS
// Camera operation fault-injection regressions against current production sources.
// Run with: powershell -NoProfile -ExecutionPolicy Bypass -File tests/e3d21/CameraReadbackContractsTests.ps1
using System;
using System.Collections.Generic;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;
using E3d21AiBridge;

internal static class CameraReadbackHarness {
 static int checks;
 static void Check(bool ok,string message) { checks++; if(!ok) throw new Exception(message); }
 static View3D Reset() { E3d21Context.NativeFault=false; E3d21Context.TestDrawList=new DrawList(); E3d21Context.TestElement=new DbElement(); return E3d21Context.TestView=new View3D(); }
 static Dictionary<string,object> Invoke(bool fit,bool dry=false) => (Dictionary<string,object>)(fit ? new E3d21Context().FitView(true,true,false) : new E3d21Context().SetView("isometric","drawlist","",1.2,dry));
 static Dictionary<string,object> State(Dictionary<string,object> r,string key) => (Dictionary<string,object>)r[key];
 static void Main() {
  foreach(bool fit in new[]{false,true}) {
   string name=fit?"fit":"set";
   var view=Reset(); var result=Invoke(fit);
   Check(Equals(result["success"],true),name+" successful readback");
   Check(Equals(result["camera_matches_request"],true)&&Equals(result["camera_readback_succeeded"],true),name+" actual agreement");
   Check(Equals(result["camera_write_completed"],true)&&view.CentreCalls==1&&view.Maps==1,name+" single camera application");
   Check(!ReferenceEquals(result["actual_camera"],result["requested_camera"]),name+" distinct actual and request");
   Check(Equals(result["rollback_attempted"],false),name+" no rollback");
   if(fit) Check(Math.Abs(((double[])result["direction"])[0]-1/Math.Sqrt(3))<1e-9 && ((double[])State(result,"requested_camera")["direction"])[0]==1.0,"fit scalar returns SDK normalized direction");

   view=Reset(); view.OnMap=()=>view.Centre=new[]{900.0,8.0,7.0}; result=Invoke(fit);
   Check(Equals(result["success"],false)&&Equals(result["camera_matches_request"],false),name+" mismatch fails");
   Check(Equals(result["camera_readback_succeeded"],true)&&view.Centre[0]==900&&view.CentreCalls==1,name+" user adjustment observed without restore");
   Check(((double[])State(result,"actual_camera")["centre"])[0]==900 && ((double[])State(result,"requested_camera")["centre"])[0]==50,name+" mismatch separation");
   Check(Equals(result["rollback_attempted"],false)&&Equals(result["retry_safe"],false),name+" mismatch no replay");
   if(fit) Check(((double[])result["centre"])[0]==900,"fit top-level actual mismatch");

   view=Reset(); view.OnMap=()=>view.FailRead=true; result=Invoke(fit);
   Check(Equals(result["success"],false)&&Equals(result["camera_readback_succeeded"],false),name+" getter failure");
   Check(result["actual_camera"]==null&&result["after"]==null&&result["camera_matches_request"]==null,name+" failure unknown, not echoed");
   Check((string)result["camera_readback_error"]=="injected getter failure"&&view.CentreCalls==1,name+" getter diagnostic no restore");
   Check(Equals(result["camera_write_completed"],true)&&Equals(result["outcome_uncertain"],true),name+" completed write differs from verified read");
   if(fit) Check(result["centre"]==null&&result["direction"]==null&&result["radius"]==null,"fit unread scalars null");

   view=Reset(); view.FailRadius=true; result=Invoke(fit);
   Check(Equals(result["success"],false)&&Equals(result["write_applied"],true)&&Equals(result["camera_write_completed"],false),name+" partial setter failure");
   Check(Equals(result["camera_readback_succeeded"],true)&&view.CentreCalls==1&&view.Maps==0,name+" partial residual observed once");
   Check((double)State(result,"actual_camera")["radius"]==10&&Equals(result["rollback_attempted"],false),name+" partial state not rolled back");

   view=Reset(); view.RejectCentre=true; result=Invoke(fit);
   Check(Equals(result["success"],false)&&result["write_applied"]==null&&view.CentreCalls==1&&view.SetterCalls==1,name+" rejected setter never replayed");

   view=Reset(); view.OnMap=()=>E3d21Context.TestView=new View3D(); result=Invoke(fit);
   Check(Equals(result["camera_readback_succeeded"],false)&&result["actual_camera"]==null&&view.CentreCalls==1,name+" view switch unknown");

   view=Reset(); view.OnMap=()=>view.OnFinalRead=()=>E3d21Context.TestView=new View3D(); result=Invoke(fit);
   Check(Equals(result["camera_readback_succeeded"],false)&&result["actual_camera"]==null,name+" switch during getter invalidates readback");

   view=Reset(); view.OnMap=()=>E3d21Context.TestDrawList.Items.Add(new DrawListMember{DbElement=new DbElement{Refno="=1/3"}}); result=Invoke(fit);
   Check(Equals(result["success"],false)&&Equals(result["membership_unchanged"],false)&&view.CentreCalls==1,name+" display inconsistency no replay");

   view=Reset(); view.OnMap=()=>E3d21Context.NativeFault=true; bool fault=false; try { Invoke(fit); } catch(InvalidOperationException ex) { fault=ex.Message=="native fault latched"; }
   Check(fault&&view.ReadCalls==1&&view.CentreCalls==1,name+" native fault halts residual SDK access");
  }
  var dryView=Reset(); var dry=Invoke(false,true);
  Check(Equals(dry["success"],true)&&dry["camera_matches_request"]==null&&Equals(dry["camera_matches_before"],true),"dry preview distinct from applied agreement");
  Check(dryView.SetterCalls==0&&dryView.Maps==0&&Equals(dry["camera_write_completed"],false),"dry preview has no setters");
  var addedView=Reset(); E3d21Context.TestDrawList.Items.Clear(); var added=(Dictionary<string,object>)new E3d21Context().FitView(true,true,true);
  Check(Equals(added["success"],true)&&Equals(added["auto_added_current"],true)&&E3d21Context.TestDrawList.AddCalls==1,"explicit empty Drawlist materialization retained");
  Console.WriteLine("PASS: "+checks+" offline camera readback checks; actual source compiled with fake SDK, no native E3D calls.");
 }
}
#endif

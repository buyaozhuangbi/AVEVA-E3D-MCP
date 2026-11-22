// Test-only SDK substitutes. This file never references or initializes AVEVA assemblies.
// Included only by CameraReadbackContractsTests.ps1 in an isolated temporary project.
#if CAMERA_READBACK_TEST_HARNESS
using System;
using System.Collections.Generic;
using System.Linq;
using Aveva.Core.Database;
using Aveva.Core3D.Graphics;
using E3d21AiBridge;

namespace Aveva.Core.Database {
 public class DbElement { public string Refno = "=1/2"; public DbElement Owner; public ElementType GetElementType() => new ElementType(); }
 public class ElementType { public bool IsWorld => true; }
 public class Spatial { public static Spatial Instance => null; public bool LimitsBox(DbElement e, out Aveva.Core.Geometry.LimitsBox b) { b=null; return false; } }
}

namespace Aveva.Core.Geometry {
 public class Point { public double X,Y,Z; }
 public class LimitsBox { public Point Minimum, Maximum; }
}
namespace Aveva.Core3D.Graphics {
 public enum LimitsType { VIEW }
 public class DrawListMember {
  public DbElement DbElement = new DbElement(); public bool ShallowRendering;
  public void GetVisualProperties(ref bool visible, ref int colour, ref int transparency, ref bool edges) { visible=true; colour=1; }
 }
 public class DrawList {
  public int ID = 7; public List<DrawListMember> Items = new List<DrawListMember> { new DrawListMember() }; public int AddCalls;
  public IEnumerable<DrawListMember> Members() => Items;
  public bool Exists(DbElement e) => Items.Any(x => x.DbElement.Refno == e.Refno);
  public void Add(DbElement e) { AddCalls++; Items.Add(new DrawListMember { DbElement=e }); }
 }
 public class DrawListManager { public static DrawListManager Instance = new DrawListManager(); public void UpdateGraphics() {} }
 public class View3D {
  public int ViewId=4; public double[] Centre = new[]{1.0,2.0,3.0}; public double[] Direction = new[]{0.0,1.0,0.0};
  public double Radius=10, Twist=2; public bool PerspectiveMode=true, ShadeMode;
  public int CentreCalls, SetterCalls, Maps, ReadCalls; public bool FailRead, FailRadius, RejectCentre; public Action OnMap, OnFinalRead;
  public bool IsValid() => true;
  public int GetAttachedDrawList() => 7;
  public double[] GetThruPoint() { ReadCalls++; if(FailRead) throw new InvalidOperationException("injected getter failure"); return Centre; }
  public double[] GetDirection() => Direction;
  public double GetRadius() => Radius;
  public double GetBearing() => 0;
  public double GetElevation() => 0;
  public double GetExtraTwistDegrees() => Twist;
  public int GetWindowWidth() => 800;
  public int GetWindowHeight() => 600;
  public double[] GetDrawlistLimits() => new[]{0.0,100.0,0.0,40.0,0.0,20.0};
  public double[] GetLimits(LimitsType t) { var a=OnFinalRead; OnFinalRead=null; if(a!=null) a(); return GetDrawlistLimits(); }
  public bool SetThruPoint(double[] c) { SetterCalls++; CentreCalls++; if(RejectCentre) return false; Centre=(double[])c.Clone(); return true; }
  public bool SetCentreAndDirNoMapUpdate(double[] c, double[] d) { if(!SetThruPoint(c)) return false; double n=Math.Sqrt(d.Sum(x=>x*x)); Direction=d.Select(x=>x/n).ToArray(); return true; }
  public void SetRadius(double r) { SetterCalls++; if(FailRadius) throw new InvalidOperationException("injected radius failure"); Radius=r; }
  public void SetPerspectiveModeNoMapUpdate(bool p) { SetterCalls++; PerspectiveMode=p; }
  public void SetExtraTwistDegrees(double t) { SetterCalls++; Twist=t; }
  public void SetShadeModeNoMapUpdate(bool s) { SetterCalls++; ShadeMode=s; }
  public void MapView() { Maps++; if(OnMap!=null) OnMap(); }
 }
}
namespace E3d21AiBridge {
 public sealed partial class E3d21Context {
  public static View3D TestView; public static DrawList TestDrawList; public static DbElement TestElement; public static bool NativeFault;
  private bool active;
  private bool IsCommandActive() => active;
  private bool TryBeginCommand(string c) { if(active) return false; active=true; return true; }
  private void EndCommand() { active=false; }
  private T OnUi<T>(Func<T> f) => f();
  private static void ThrowIfNativeFaulted() { if(NativeFault) throw new InvalidOperationException("native fault latched"); }
  private static T NativeRead<T>(Func<T> f) { ThrowIfNativeFaulted(); T v=f(); ThrowIfNativeFaulted(); return v; }
  private static void NativeWrite(Action a) { ThrowIfNativeFaulted(); a(); ThrowIfNativeFaulted(); }
  private static object CurrentView3D() => TestView;
  private static DrawList CurrentDrawList() => TestDrawList;
  private static DbElement CurrentElement() => TestElement;
  private static string ElementRef(DbElement e) => e?.Refno;
  private static string ElementType(DbElement e) => "EQUI";
  private static bool IsUsable(DbElement e) => e!=null;
  private static DbElement ResolveElement(string r) => TestElement;
  private static Dictionary<string,object> DrawListPersistenceResult(string s) => new Dictionary<string,object>{{"status",s}};
  private static Dictionary<string,object> SaveDrawListState(DrawList d) => DrawListPersistenceResult("saved");
 }
}
#endif

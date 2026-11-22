// Compiled only with recording SDK fakes. No AVEVA database assembly is loaded.
internal static class ParagonCatalogueManifestHarness
{
    private static readonly string[] AllAttributes = { "DESC", "HEIG", "LOCK", "SEQU", "ARR" };
    private const string Session = "paragon-offline-session";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message + "\nRecorded SDK calls: " + string.Join(", ", Scenario.Reads));
    }

    private static bool Null(JToken value) => value == null || value.Type == JTokenType.Null;

    private static void Reset()
    {
        Scenario.Reset();
        Scenario.Names.Clear(); Scenario.Elements.Clear(); Scenario.Zone.Children.Clear();
        Scenario.Root = Scenario.Add(1, "CATA", Scenario.Zone, "/CAT");
        Scenario.Left = Scenario.Add(2, "SECT", Scenario.Root, "/CAT/S");
        Scenario.Grandchild = Scenario.Add(3, "CATE", Scenario.Left, "/CAT/S/C");
        Scenario.Add(4, "SCOM", Scenario.Grandchild, "/CAT/S/C/COMP");
        Scenario.Right = Scenario.Add(5, "STSE", Scenario.Root, "/CAT/STSE");
        foreach (int number in new[] { 1, 2, 3, 4, 5, 99 }) Scenario.Overrides["e" + number + ".db_type"] = DbType.Catalogue;
        Scenario.Overrides["e99.type.short"] = "WORL";
    }

    private static JObject Read(string refno = "=1/1", int depth = 8, int limit = 64, string[] attributes = null)
    {
        var context = new E3d21Context();
        object result = context.ReadParagonCatalogueManifest(refno, depth, limit, attributes, Session);
        Check(Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0, "Inspection requires exactly one read-only UI callback.");
        var reply = JObject.FromObject(new
        {
            ok = true, version = "E3D21", session_id = Session,
            bridge_build = typeof(E3d21Context).Module.ModuleVersionId.ToString("D"), result
        });
        Console.WriteLine("Paragon native receipt: " + JObject.FromObject(new
        {
            payload = new { refno, max_depth = depth, limit, attributes = attributes ?? new string[0] }, reply
        }).ToString(Formatting.None));
        return (JObject)reply["result"];
    }

    private static void Validation()
    {
        var invalid = new Action[]
        {
            () => ParagonCatalogueManifestRequest.Validate(null, 8, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("CE", 8, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("/", 8, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("/CAT?", 8, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("/CAT/*", 8, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("/CAT X", 8, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("=0/1", 8, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("=2147483648/1", 8, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("=1/-1", 8, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("/CAT", 0, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("/CAT", 17, 64, null),
            () => ParagonCatalogueManifestRequest.Validate("/CAT", 8, 0, null),
            () => ParagonCatalogueManifestRequest.Validate("/CAT", 8, 101, null),
            () => ParagonCatalogueManifestRequest.Validate("/CAT", 8, 100, new[] { "A", "B", "C", "D", "E", "F" }),
            () => ParagonCatalogueManifestRequest.Validate("/CAT", 8, 64, new[] { "DESC", " desc " }),
            () => ParagonCatalogueManifestRequest.Validate("/CAT", 8, 64, new[] { "DESC WRT" }),
            () => ParagonCatalogueManifestRequest.Validate("/CAT", 8, 64, new[] { "DÉSC" }),
            () => new E3d21Context().ReadParagonCatalogueManifest("/CAT", 8, 64, null, "")
        };
        foreach (var request in invalid)
        {
            Reset();
            bool rejected = false;
            try { request(); } catch (ArgumentException) { rejected = true; } catch (InvalidOperationException) { rejected = true; }
            Check(rejected && Scenario.Dispatches == 0 && Scenario.Reads.Count == 0, "Invalid input reached the SDK.");
        }
        var normalized = ParagonCatalogueManifestRequest.Validate(" =01/000 ", 16, 100, new[] { " a ", "B", "C", "D", "E" });
        Check(normalized.Refno == "=1/0" && normalized.Attributes[0] == "A", "Canonical numeric references or exact attribute budget changed.");
        foreach (bool onUi in new[] { false, true })
        {
            Reset();
            Scenario.Busy = !onUi; Scenario.BusyOnUi = onUi;
            bool rejected = false;
            try { new E3d21Context().ReadParagonCatalogueManifest("/CAT", 8, 64, null, Session); }
            catch (InvalidOperationException) { rejected = true; }
            Check(rejected && Scenario.Dispatches == (onUi ? 1 : 0) && Scenario.Reads.Count == 0, "A busy host reached database reads.");
        }
    }

    private static void NativeTypeAndIdentity()
    {
        Reset();
        JObject result = Read(" /cat ", attributes: AllAttributes);
        var rows = result["rows"];
        Check((bool)result["complete"] && (int)result["returned_count"] == 5 && !(bool)result["atomic_snapshot"], "A verified bounded catalogue observation was lost.");
        Check((string)rows[0]["type"] == "CATA" && (string)rows[1]["type"] == "SECT" && (string)rows[2]["type"] == "CATE" &&
            (string)rows[4]["type"] == "STSE" && (string)rows[0]["db_type"] == "Catalogue" && (int)rows[0]["db_type_code"] == 2,
            "The native Catalogue database and ordered types were not retained.");
        Check((string)rows[0]["owner"]["type"] == "WORL" && Null(rows[0]["sibling_index"]) &&
            (int)rows[3]["parent_index"] == 2 && (int)rows[4]["sibling_index"] == 2,
            "Native owner, parent or Member ordinal evidence was lost.");
        Check((string)result["context"]["before"]["current_element"]["db_type"] == "Design" &&
            Null(result["scope"]["current_module"]) && Null(result["scope"]["database_creation_verified"]),
            "Catalogue observation must preserve a Design CE and unknown module/creation state.");
        Check(Scenario.Reads.Contains("mdb.catalogue.find./cat") &&
            Scenario.Reads.Count(key => key == "attribute.resolve.DESC") == 1 &&
            (double)rows[0]["typed_values"]["HEIG"] == 42.25 && (bool)rows[0]["typed_values"]["LOCK"] == false &&
            (int)rows[0]["typed_values"]["SEQU"] == 0 && Null(rows[0]["typed_values"]["ARR"]),
            "Named roots must use the typed Catalogue lookup; attributes must retain their native scalar types.");

        foreach (string kind in new[] { "Design CATA", "Draft LIBY", "missing", "wrong reference", "wrong name" })
        {
            Reset();
            string target = "=1/1";
            if (kind == "Design CATA") Scenario.Overrides["e1.db_type"] = DbType.Design;
            else if (kind == "Draft LIBY") { Scenario.Overrides["e1.db_type"] = DbType.Draft; Scenario.Overrides["e1.type.short"] = "LIBY"; }
            else if (kind == "missing") Scenario.Overrides["element.get.=1/1"] = null;
            else if (kind == "wrong reference") Scenario.Elements["=1/1"] = Scenario.Left;
            else { target = "/CAT"; Scenario.Names["/CAT"] = Scenario.Left; }
            bool rejected = false;
            try { new E3d21Context().ReadParagonCatalogueManifest(target, 8, 64, null, Session); }
            catch (ArgumentException) { rejected = true; } catch (InvalidOperationException) { rejected = true; }
            Check(rejected && Scenario.Dispatches == 1 && !Scenario.Reads.Any(key => key.Contains(".member.")),
                "An unverified or non-Catalogue root was enumerated: " + kind);
        }
    }

    private static void Bounds()
    {
        Reset();
        JObject result = Read(depth: 3, limit: 5);
        Check((bool)result["complete"] && !(bool)result["truncated"], "An exact fit at both budgets must be complete.");
        foreach (var bounds in new[] { (1, 64, "depth"), (8, 2, "limit"), (1, 2, "depth,limit") })
        {
            Reset();
            result = Read(depth: bounds.Item1, limit: bounds.Item2, attributes: new[] { "DESC" });
            Check(!(bool)result["complete"] && (bool)result["truncated"] && (int)result["returned_count"] == 2 &&
                string.Join(",", result["truncation_reasons"].Values<string>()) == bounds.Item3 &&
                !Scenario.Reads.Contains("e3.text.DESC") && !Scenario.Reads.Contains("e1.member.2"),
                "Traversal must stop at the first omitted member and must not read its attributes.");
        }
        Reset();
        Scenario.Root.Children.Clear();
        result = Read(limit: 1, depth: 1);
        Check((bool)result["complete"] && (int)result["returned_count"] == 1 && Scenario.Reads.Contains("e1.member.1"),
            "An explicit empty catalogue is a verified one-row observation, including at the root budget.");
        Reset();
        Scenario.Root.Children.Clear();
        for (int n = 1000; n < 1200; n++)
        {
            Scenario.Add(n, "SECT", Scenario.Root);
            Scenario.Overrides["e" + n + ".db_type"] = DbType.Catalogue;
        }
        result = Read(limit: 100);
        Check((int)result["returned_count"] == 100 && (bool)result["truncated"] &&
            !Scenario.Reads.Contains("e1.member.101") && !Scenario.Reads.Contains("e1099.member.1"),
            "The total node budget must include the root and allow only one omitted-node probe.");
    }

    private static void PartialAndUnknown()
    {
        foreach (string kind in new[] { "wrong owner", "wrong database", "repeated member", "unreadable member" })
        {
            Reset();
            if (kind == "wrong owner") Scenario.Grandchild.Parent = Scenario.Root;
            else if (kind == "wrong database") Scenario.Overrides["e3.db_type"] = DbType.Design;
            else if (kind == "repeated member") Scenario.Left.Children.Add(Scenario.Grandchild);
            else Scenario.Overrides["e2.member.1"] = new InvalidOperationException("member unreadable");
            JObject result = Read();
            Check(!(bool)result["complete"] && !(bool)result["traversal_complete"] && !(bool)result["truncated"] &&
                result["read_errors"].Count() == 1 && !Scenario.Reads.Contains("e1.member.2"),
                "A hierarchy failure must retain only the verified prefix: " + kind);
        }

        Reset();
        Scenario.Overrides["e1.owner"] = new InvalidOperationException("root owner unreadable");
        Scenario.Overrides["e2.text.NAME"] = null;
        Scenario.Overrides["e3.double.HEIG"] = double.NaN;
        Scenario.Overrides["e4.supports.DESC"] = false;
        Scenario.Overrides["e5.text.DESC"] = new string('X', 4097);
        JObject partial = Read(attributes: AllAttributes);
        Check(!(bool)partial["complete"] && (bool)partial["traversal_complete"] &&
            !Null(partial["rows"][0]["owner_error"]) && !Null(partial["rows"][1]["name_error"]) &&
            !Null(partial["rows"][2]["typed_read_errors"]["HEIG"]) && !Null(partial["rows"][2]["values"]["HEIG"]) &&
            (string)partial["rows"][3]["unavailable_attributes"][0] == "DESC" && !Scenario.Reads.Contains("e4.text.DESC") &&
            !Null(partial["rows"][4]["errors"]["DESC"]), "Independent unknowns must preserve other native row evidence.");

        Reset();
        partial = Read(attributes: new[] { ":UNDEFINED", "DESC" });
        Check(!(bool)partial["complete"] && !Null(partial["rows"][0]["errors"][":UNDEFINED"]) &&
            !Null(partial["rows"][0]["values"]["DESC"]), "An unknown SDK attribute must stay an explicit per-row error.");

        foreach (string change in new[] { "unknown before CE", "unknown after MDB", "typed CE", "same-name MDB", "same-name project" })
        {
            Reset();
            if (change == "unknown before CE") Scenario.Ce = null;
            else Scenario.OnRead = key =>
            {
                if (key == "mdb.current" && change == "unknown after MDB" && Scenario.Reads.Count(k => k == key) == 2)
                    Scenario.Overrides[key] = new InvalidOperationException("current MDB unreadable");
                if (key != "e1.member.1") return;
                if (change == "typed CE") { Scenario.Ce = new DbElement(90, "BOX"); }
                else if (change == "same-name MDB") Scenario.Mdb = new MDB();
                else if (change == "same-name project") Scenario.Project = new Project();
            };
            JObject result = Read();
            Check(!(bool)result["complete"] && (bool)result["traversal_complete"] && (int)result["returned_count"] == 5,
                "Boundary uncertainty or drift must preserve returned rows: " + change);
            if (change.StartsWith("unknown", StringComparison.Ordinal))
                Check(Null(result["context"]["boundary_agreement"]), "Unreadable context must remain unknown.");
            else Check(!(bool)result["context"]["boundary_agreement"], "Changed typed context must be rejected.");
        }
    }

    private static void FaultMatrix()
    {
        Reset();
        Read(attributes: AllAttributes);
        int calls = Scenario.Reads.Count;
        for (int failAt = 1; failAt <= calls; failAt++)
            foreach (bool ordinaryException in new[] { false, true })
            {
                Reset();
                var context = new E3d21Context();
                Scenario.OnRead = key =>
                {
                    if (Scenario.Reads.Count != failAt) return;
                    context.Fault();
                    if (ordinaryException) Scenario.Overrides[key] = new InvalidOperationException("ordinary exception alongside native fault");
                };
                bool stopped = false;
                try { context.ReadParagonCatalogueManifest("=1/1", 8, 64, AllAttributes, Session); }
                catch (NativeDatabaseFaultException) { stopped = true; }
                Check(stopped && Scenario.Dispatches == 1 && Scenario.Reads.Count == failAt && Scenario.ForbiddenCalls == 0,
                    "A native fault must dominate ordinary errors and stop all subsequent SDK access at call " + failAt);
                int dispatched = Scenario.Dispatches;
                try { context.ReadParagonCatalogueManifest("=1/1", 8, 64, AllAttributes, Session); }
                catch (NativeDatabaseFaultException) { }
                Check(Scenario.Dispatches == dispatched && Scenario.Reads.Count == failAt, "A latched native fault must reject follow-up dispatch.");
            }
        Console.WriteLine("Paragon native fault matrix: " + (calls * 2) + " injection cases.");
    }

    private static int Main()
    {
        Validation(); NativeTypeAndIdentity(); Bounds(); PartialAndUnknown(); FaultMatrix();
        Console.WriteLine("Paragon catalogue manifest production checks passed.");
        return 0;
    }
}

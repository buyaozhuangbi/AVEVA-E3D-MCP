// This main is compiled with the SDK and HTTP fakes from EquipmentManifestHarness.cs.
internal sealed class E3d21HttpAdapter { }

internal static class PipeMemberManifestHarness
{
    private const string DefaultRequest = "{\"refno\":\"=01/001\"}";
    private const string IdentityOnly = "{\"refno\":\"/P\",\"attributes\":[]}";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message + "\nSDK reads: " + string.Join(", ", Scenario.Reads));
    }

    private static bool IsNull(JToken token) => token == null || token.Type == JTokenType.Null;

    private static void ResetPipe()
    {
        Scenario.Reset();
        Scenario.Names.Clear();
        Scenario.Elements.Clear();
        Scenario.Zone.Children.Clear();
        Scenario.Root = Scenario.Add(1, "PIPE", Scenario.Zone, "/P");
        Scenario.Left = Scenario.Add(2, "BRAN", Scenario.Root, "/P/B");
        Scenario.Right = Scenario.Add(3, "FLAN", Scenario.Left, "=1/3");
        Scenario.Grandchild = Scenario.Add(33, "TUBI", Scenario.Left, "ileave tube of =1/3");
        Scenario.Overrides["e33.ref"] = new[] { 1, 3 };
        foreach (string field in new[] { "POS", "ORI", "SPREF" })
            Scenario.Attributes[field] = new DbAttribute(field, DbAttributeType.ELEMENT);
        foreach (string field in new[] { "ABOR", "LBOR" })
            Scenario.Attributes[field] = new DbAttribute(field, DbAttributeType.DOUBLE);
        Scenario.Overrides["e3.text.SPREF"] = "/A3B/FWD80";
        Scenario.Overrides["e33.text.SPREF"] = "/A3B/PA80";
        Scenario.Overrides["e3.double.ABOR"] = 80d;
        Scenario.Overrides["e33.double.ABOR"] = 80d;
        Scenario.Overrides["e2.supports.SPREF"] = false;
    }

    private static JObject Read(string json = DefaultRequest, E3d21Context context = null)
    {
        var response = ManifestRoute.RunAsync(json, context ?? new E3d21Context()).GetAwaiter().GetResult();
        Check(response.StatusCode == 200, "The read route failed.");
        Check(Scenario.Dispatches == 1 && Scenario.ForbiddenCalls == 0, "Expected one read-only SDK dispatch.");
        return JObject.FromObject(response.Value);
    }

    private static void Reject(string json, int status, object context, bool query = false)
    {
        var response = ManifestRoute.RunAsync(json, context, query, handleErrors: true).GetAwaiter().GetResult();
        Check(response.StatusCode == status, "HTTP status did not match rejection: " + json);
        Check(Scenario.Dispatches == 0 && Scenario.Reads.Count == 0 && Scenario.ForbiddenCalls == 0,
            "Request rejection reached the SDK.");
    }

    private static void VerifyRequestValidation()
    {
        foreach (string json in new[] { "{", "{}", "{\"refno\":\"CE\"}", "{\"refno\":\"/P?\"}",
            "{\"refno\":\"=0/1\"}", "{\"refno\":\"/P\",\"refno\":\"/P/B\"}",
            "{\"refno\":\"/P\",\"attributes\":null}", "{\"refno\":\"/P\",\"attributes\":[\"POS\",\" pos \"]}",
            "{\"refno\":\"/P\",\"limit\":0}", "{\"refno\":\"/P\",\"max_depth\":17}",
            "{\"refno\":\"/P\",\"limit\":1.0}", "{\"refno\":\"/P\",\"command\":\"ignored\"}",
            "{\"refno\":\"/P\",\"limit\":100,\"attributes\":[\"A\",\"B\",\"C\",\"D\",\"E\",\"F\"]}",
            "{\"refno\":\"" + new string('A', 8192) + "\"}" })
        {
            ResetPipe();
            Reject(json, 400, new E3d21Context());
        }
        ResetPipe();
        Reject(IdentityOnly, 400, new E3d21Context(), query: true);
        ResetPipe();
        Reject(IdentityOnly, 404, new object());
        ResetPipe();
        Scenario.Busy = true;
        Reject(IdentityOnly, 500, new E3d21Context());
    }

    private static void VerifyTraversal()
    {
        ResetPipe();
        var envelope = Read();
        JToken result = envelope["result"], rows = result["rows"];
        Check((string)result["requested_refno"] == "=1/1" && (string)result["root"]["name"] == "/P" &&
            (bool)result["complete"] && (bool)result["traversal_complete"] && !(bool)result["atomic_snapshot"],
            "Verified, non-atomic PIPE traversal was lost.");
        Check(!(bool)result["all_returned_values_available"] && (bool)rows[0]["complete"] &&
            (string)rows[0]["unavailable_attributes"][0] == "SPREF" && !Scenario.Reads.Contains("e2.text.SPREF"),
            "An unavailable field must be separate from a failed SDK read.");
        Check((int)result["returned_count"] == 3 && (int)rows[2]["depth"] == 2 && (int)rows[2]["sibling_index"] == 2 &&
            (int)rows[2]["parent_index"] == 1 && (string)rows[1]["identity_key"] == "=1/3|FLAN" &&
            (string)rows[2]["identity_key"] == "=1/3|TUBI" && (string)rows[2]["name"] == "ileave tube of =1/3",
            "The component and the implicit tube must keep separate ordered identities.");
        Check((string)rows[1]["values"]["SPREF"] == "/A3B/FWD80" && (string)rows[2]["values"]["SPREF"] == "/A3B/PA80" &&
            (double)rows[2]["typed_values"]["ABOR"] == 80 && Scenario.Reads.Count(k => k.StartsWith("element.get.")) == 1,
            "Read attributes from the actual member handles with no numeric re-resolution.");
        Console.WriteLine("Pipe manifest receipt: " + envelope.ToString(Formatting.None));

        ResetPipe();
        result = Read("{\"refno\":\"/p/b\",\"limit\":2,\"max_depth\":1,\"attributes\":[]}")["result"];
        Check((string)result["root"]["type"] == "BRAN" && (bool)result["complete"] && (int)result["returned_count"] == 2,
            "An exact fit at both limits is complete when no member is omitted.");

        foreach (var bounds in new[] { (1, 64, "depth"), (8, 1, "limit"), (1, 1, "depth,limit") })
        {
            ResetPipe();
            result = Read("{\"refno\":\"/P\",\"attributes\":[],\"max_depth\":" + bounds.Item1 +
                          ",\"limit\":" + bounds.Item2 + "}")["result"];
            Check(!(bool)result["complete"] && (bool)result["truncated"] && (int)result["returned_count"] == 1 &&
                string.Join(",", result["truncation_reasons"].Values<string>()) == bounds.Item3 &&
                !Scenario.Reads.Contains("e2.member.2"), "Traversal must stop at the first known omission.");
        }

        ResetPipe();
        Scenario.Left.Children.Clear();
        result = Read("{\"refno\":\"/P/B\"}")["result"];
        Check((bool)result["complete"] && (int)result["returned_count"] == 0 &&
            !Scenario.Reads.Any(read => read.StartsWith("attribute.resolve.")), "Empty BRAN should return verified empty rows.");
    }

    private static void VerifyFailures()
    {
        foreach (string kind in new[] { "typed owner", "repeated member", "member read" })
        {
            ResetPipe();
            if (kind == "typed owner")
            {
                var ownerAlias = new DbElement(22, "TUBI");
                Scenario.Overrides["e22.ref"] = new[] { 1, 2 };
                Scenario.Grandchild.Parent = ownerAlias;
            }
            else if (kind == "repeated member") Scenario.Left.Children[1] = Scenario.Right;
            else Scenario.Overrides["e2.member.2"] = new InvalidOperationException("Failed to read next member");
            JToken result = Read(IdentityOnly)["result"];
            Check(!(bool)result["traversal_complete"] && !(bool)result["truncated"] && (int)result["returned_count"] == 2 &&
                (string)result["read_errors"][0]["stage"] == "member" && (int)result["read_errors"][0]["sibling_index"] == 2 &&
                !Scenario.Reads.Contains("e1.member.2"), "Hierarchy failures retain only the verified prefix: " + kind);
        }

        ResetPipe();
        Scenario.Overrides["e3.double.ABOR"] = double.NaN;
        Scenario.Overrides["e33.text.NAME"] = null;
        Scenario.Overrides["e33.text.POS"] = new string('X', 4097);
        JToken partial = Read("{\"refno\":\"/P\",\"attributes\":[\"SPREF\",\"ABOR\",\"POS\",\":UNKNOWN\"]}")["result"];
        Check((bool)partial["traversal_complete"] && !(bool)partial["complete"] &&
            !IsNull(partial["rows"][1]["typed_read_errors"]["ABOR"]) && !IsNull(partial["rows"][1]["values"]["ABOR"]) &&
            !IsNull(partial["rows"][2]["name_error"]) && !IsNull(partial["rows"][2]["errors"]["POS"]) &&
            !IsNull(partial["rows"][2]["values"]["SPREF"]) && !IsNull(partial["rows"][2]["errors"][":UNKNOWN"]),
            "Independent attribute and name failures must preserve the other row evidence.");

        foreach (string change in new[] { "same-name MDB", "typed CE", "missing CE" })
        {
            ResetPipe();
            Scenario.Ce = Scenario.Right;
            Scenario.OnRead = key =>
            {
                if (key != "e1.member.1") return;
                if (change == "same-name MDB") Scenario.Mdb = new MDB();
                else if (change == "typed CE") Scenario.Ce = Scenario.Grandchild;
                else Scenario.Ce = null;
            };
            JToken result = Read(IdentityOnly)["result"], context = result["context"];
            Check((bool)result["traversal_complete"] && !(bool)result["complete"] && (int)result["returned_count"] == 3 &&
                (string)result["read_errors"][0]["stage"] == "context_after" &&
                (bool?)context["boundary_agreement"] == (change == "missing CE" ? (bool?)null : false),
                "Lost context uncertainty or verified rows: " + change);
            if (change == "same-name MDB")
                Check(!(bool)context["same_mdb_instance"] && JToken.DeepEquals(context["before"], context["after"]),
                    "Replacing an MDB instance must be detected even when names match.");
        }

        foreach (string target in new[] { "/ZONE", "/P", "=1/1" })
        {
            ResetPipe();
            if (target == "/ZONE") Scenario.Names[target] = Scenario.Zone;
            else if (target == "/P") Scenario.Names[target] = Scenario.Left;
            else Scenario.Elements[target] = Scenario.Left;
            var response = ManifestRoute.RunAsync("{\"refno\":\"" + target + "\"}", new E3d21Context(), handleErrors: true)
                .GetAwaiter().GetResult();
            Check(response.StatusCode != 200 && Scenario.Dispatches == 1 && !Scenario.Reads.Any(read => read.Contains(".member.")) &&
                Scenario.ForbiddenCalls == 0, "An unrelated or non-piping root must not enumerate members.");
        }

        ResetPipe();
        var faulted = new E3d21Context();
        Scenario.OnRead = key => { if (key == "e3.double.ABOR") faulted.Fault(); };
        try
        {
            Read(DefaultRequest, faulted);
            throw new Exception("The native fault escaped as a normal manifest.");
        }
        catch (NativeDatabaseFaultException)
        {
            Check(Scenario.Reads.Last() == "e3.double.ABOR" && Scenario.Dispatches == 1,
                "No SDK reads may continue after a native fault.");
        }
    }

    private static int Main()
    {
        VerifyRequestValidation();
        VerifyTraversal();
        VerifyFailures();
        Console.WriteLine("Pipe member manifest production checks passed.");
        return 0;
    }
}

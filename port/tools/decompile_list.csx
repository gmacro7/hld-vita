// Read-only: decompile the code entries listed in HLD_DECOMPILE_LIST into HLD_DECOMPILE_OUT, one file each.
// Each entry gets HLD_DECOMPILE_SECONDS (default 60) before it is skipped; the data file is never saved.
using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using UndertaleModLib.Decompiler;
EnsureDataLoaded();
string listPath = Environment.GetEnvironmentVariable("HLD_DECOMPILE_LIST");
string outDir = Environment.GetEnvironmentVariable("HLD_DECOMPILE_OUT");
int seconds = int.TryParse(Environment.GetEnvironmentVariable("HLD_DECOMPILE_SECONDS"), out var s) ? s : 60;
Directory.CreateDirectory(outDir);
var context = new GlobalDecompileContext(Data);
var settings = Data.ToolInfo.DecompilerSettings;
foreach (var name in File.ReadAllLines(listPath).Select(l => l.Trim()).Where(l => l.Length > 0)) {
    var code = Data.Code.ByName(name);
    if (code == null) { Console.WriteLine($"MISSING {name}"); continue; }
    var task = Task.Run(() => new Underanalyzer.Decompiler.DecompileContext(context, code, settings).DecompileToString());
    try {
        if (task.Wait(TimeSpan.FromSeconds(seconds))) {
            File.WriteAllText(Path.Combine(outDir, name + ".gml"), task.Result);
            Console.WriteLine($"OK {name}");
        } else {
            Console.WriteLine($"TIMEOUT {name}");
        }
    } catch (Exception e) {
        Console.WriteLine($"FAILED {name}: {e.InnerException?.Message ?? e.Message}");
    }
}

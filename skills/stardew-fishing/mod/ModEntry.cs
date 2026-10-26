using System.Text.Json;
using HarmonyLib;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Tools;

namespace StandaloneFishingSkill;

public sealed class ModEntry : Mod
{
    internal static ModEntry? Instance;
    internal FishingRod? Rod;
    internal bool Active;
    private readonly JsonSerializerOptions json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, PropertyNameCaseInsensitive = true };
    private readonly Queue<string> seen = new();
    private string runtime = "", runId = "", state = "idle", phase = "idle", reason = "";
    private object? ack;
    private Limits limits = new();
    private GameLocation? location;
    private Point origin;
    private string date = "";
    private string pausedDate = "";
    private int casts, fish, trash, failures, emptyCasts, facing;
    private bool inCast, sawWater, landing, sawBar, hookSent, catchInCast;
    private DateTime started, castStarted, nextCast;
    private Dictionary<string, int> beforeCatch = new();
    private readonly List<object> catches = new();
    // 附加模块：只在按键/命令时启动；不移动、不寻路（寻路交给 travel 等其它模块）。
    private const SButton StartKey = SButton.F7;
    private const SButton StopKey = SButton.F8;
    private string triggeredBy = "";

    public override void Entry(IModHelper helper)
    {
        Instance = this;
        runtime = Path.Combine(helper.DirectoryPath, "runtime");
        Directory.CreateDirectory(runtime);
        new Harmony(ModManifest.UniqueID).Patch(AccessTools.Method(typeof(BobberBar), nameof(BobberBar.update)),
            prefix: new HarmonyMethod(typeof(BarInput), nameof(BarInput.Prefix)),
            finalizer: new HarmonyMethod(typeof(BarInput), nameof(BarInput.Finalizer)));
        helper.Events.GameLoop.UpdateTicking += Tick;
        helper.Events.Input.ButtonPressed += (_, e) =>
        {
            if (!Context.IsWorldReady) return;
            if (e.Button == StartKey) { StartFromKey(); return; }
            if (e.Button != StopKey) return;
            pausedDate = DateKey();
            if (Active) Finish("stopped", "manual_f8");
            else { state = "stopped"; reason = "manual_f8"; WriteStatus(); }
        };
        helper.Events.GameLoop.ReturnedToTitle += (_, _) => Finish("stopped", "save_unloaded");
        helper.ConsoleCommands.Add("fish_skill_start", "在当前站位开始钓鱼（等同按 F7）", (_, _) => StartFromKey());
        helper.ConsoleCommands.Add("fish_skill_stop", "停止独立钓鱼技能（等同按 F8）", (_, _) => Finish("stopped", "console_stop"));
        WriteStatus();
        Monitor.Log($"独立钓鱼附加模块就绪：站到岸边朝水面按 {StartKey} 钓一段，{StopKey} 停止；本模块不移动、不寻路。", LogLevel.Info);
    }

    private string DateKey() => $"{Game1.player.UniqueMultiplayerID}/{Game1.year}/{Game1.currentSeason}/{Game1.dayOfMonth}";
    private int EmptySlots() => Math.Max(0, Game1.player.MaxItems - Game1.player.Items.Count(i => i != null));
    private bool OtherController() => Helper.Reflection.GetField<StardewValley.Util.IInputSimulator?>(typeof(Game1), "inputSimulator").GetValue() != null;

    private void Tick(object? sender, UpdateTickingEventArgs e)
    {
        try
        {
            if (e.IsMultipleOf(10)) ReadCommand();
            if (Active) Advance();
            if (e.IsMultipleOf(30)) WriteStatus();
        }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
        {
            // 客户端正在读 status.json 时，原子替换可能被拒；文件争用不该终止任务。
            Monitor.Log($"文件正被占用，本轮跳过：{ex.GetType().Name}", LogLevel.Trace);
        }
        catch (Exception ex)
        {
            Monitor.Log($"钓鱼受阻：{ex.GetType().Name}: {ex.Message}", LogLevel.Warn);
            Finish("blocked", "execution_error:" + ex.GetType().Name);
        }
    }

    private void ReadCommand()
    {
        var path = Path.Combine(runtime, "command.json");
        if (!File.Exists(path)) return;
        var claimed = Path.Combine(runtime, "processing.json");
        // 客户端可能刚写完文件（或文件正被占用）：拿不到就下个 tick 再试，不要抛异常。
        if (!TryMove(path, claimed)) return;
        try
        {
            var cmd = JsonSerializer.Deserialize<Command>(File.ReadAllText(claimed), json) ?? throw new ArgumentException("空指令");
            if (string.IsNullOrEmpty(cmd.Id) || cmd.Id.Length > 64) throw new ArgumentException("缺少请求ID");
            if (seen.Contains(cmd.Id)) { WriteStatus(); return; }
            seen.Enqueue(cmd.Id);
            while (seen.Count > 64) seen.Dequeue();
            if (cmd.Action == "stop")
            {
                if (!string.IsNullOrEmpty(cmd.RunId) && cmd.RunId != runId)
                    ack = new { id = cmd.Id, accepted = false, reason = "different_run" };
                else
                {
                    Finish("stopped", "requested_stop");
                    ack = new { id = cmd.Id, accepted = true, reason = "stopped" };
                }
            }
            else if (cmd.Action != "start") ack = new { id = cmd.Id, accepted = false, reason = "unknown_action" };
            else if (Math.Abs((DateTime.UtcNow - cmd.IssuedAt).TotalSeconds) > 30)
                ack = new { id = cmd.Id, accepted = false, reason = "expired_command" };
            else if (Active) ack = new { id = cmd.Id, accepted = false, reason = "already_running" };
            else
            {
                string? why;
                triggeredBy = "command";
                try { why = Start(cmd.Id, cmd.Limits); }
                catch (ArgumentException) { why = "invalid_limits"; }
                ack = new { id = cmd.Id, accepted = why == null, reason = why ?? "started" };
            }
            WriteStatus();
        }
        finally { TryDelete(claimed); }
    }

    /// <summary>按键触发：在当前站位钓一段。不做任何寻路，站不到水边就明确报原因。</summary>
    private void StartFromKey()
    {
        if (!Context.IsWorldReady) return;
        if (Active) { Monitor.Log("钓鱼已在进行中，按 F8 可以停止。", LogLevel.Info); return; }
        var limits = new Limits();
        triggeredBy = "key";
        string? why;
        try { why = Start("key-" + DateTime.UtcNow.ToString("yyyyMMddHHmmss"), limits); }
        catch (ArgumentException) { why = "invalid_limits"; }
        if (why == null)
        {
            Monitor.Log($"开始钓鱼：最多 {limits.MaxCasts} 竿，按 F8 停止。", LogLevel.Info);
            return;
        }
        state = why == "no_fishable_water_ahead_move_to_shore" ? "ready" : "stopped";
        reason = why;
        Log("start_rejected", new { why });
        WriteStatus();
        Monitor.Log("现在不能钓鱼：" + Explain(why), LogLevel.Warn);
    }

    private static string Explain(string why) => why switch
    {
        "player_not_free" => "人物正在动画、菜单或过场里，等画面能动再按。",
        "single_player_only" => "多人存档不支持自动钓鱼。",
        "menu_or_event" => "先关掉菜单/结束事件再按。",
        "other_controller_active" => "有其它自动控制正在接管角色。",
        "missing_fishing_rod" => "背包里没有鱼竿。",
        "need_one_empty_slot" => "背包一格空位都没有了：先腾出一格再按。",
        "location_not_fishable" => "这张地图不能钓鱼。",
        "energy_or_bedtime" => "体力不足或已到收工时间。",
        "no_fishable_water_ahead_move_to_shore" => "面前 2~6 格内没有可钓水面：走到岸边、朝向水面后再按。",
        "invalid_limits" => "参数超出允许范围。",
        _ => why
    };

    private string? Start(string id, Limits requested)
    {
        requested.Validate();
        if (!Context.IsWorldReady || !Context.IsPlayerFree) return "player_not_free";
        if (Context.IsMultiplayer) return "single_player_only";
        if (Game1.activeClickableMenu != null || Game1.eventUp || Game1.fadeToBlack) return "menu_or_event";
        if (OtherController()) return "other_controller_active";
        var slot = Enumerable.Range(0, Game1.player.Items.Count).FirstOrDefault(i => Game1.player.Items[i] is FishingRod, -1);
        if (slot < 0) return "missing_fishing_rod";
        if (EmptySlots() < 1) return "need_one_empty_slot";
        if (!Game1.currentLocation.canFishHere()) return "location_not_fishable";
        if (Game1.player.Stamina < requested.MinEnergy || Game1.timeOfDay >= requested.StopAt) return "energy_or_bedtime";
        var direction = ChooseFacing(requested.Facing);
        if (direction < 0) return "no_fishable_water_ahead_move_to_shore";
        limits = requested;
        Game1.player.CurrentToolIndex = slot;
        Rod = (FishingRod)Game1.player.CurrentTool;
        location = Game1.currentLocation;
        origin = Game1.player.TilePoint;
        date = DateKey();
        facing = direction;
        casts = fish = trash = failures = emptyCasts = 0;
        inCast = sawWater = landing = sawBar = false;
        catches.Clear();
        beforeCatch.Clear();
        started = nextCast = DateTime.UtcNow;
        runId = id;
        state = "running";
        phase = "ready";
        reason = "";
        Active = true;
        Log("started");
        return null;
    }

    private int ChooseFacing(int requested)
    {
        var best = -1;
        var score = 0;
        var vectors = new[] { new Point(0, -1), new Point(1, 0), new Point(0, 1), new Point(-1, 0) };
        foreach (var d in requested < 0 ? new[] { 0, 1, 2, 3 } : new[] { requested })
        {
            var n = 0;
            for (var distance = 2; distance <= 6; distance++)
            {
                var tile = Game1.player.TilePoint + new Point(vectors[d].X * distance, vectors[d].Y * distance);
                if (Game1.currentLocation.isTileOnMap(tile.ToVector2()) && Game1.currentLocation.isTileFishable(tile.X, tile.Y)) n++;
            }
            if (n > score) { best = d; score = n; }
        }
        return best;
    }

    private void Advance()
    {
        if (!Context.IsWorldReady || location != Game1.currentLocation || date != DateKey()) { Finish("stopped", "scene_or_date_changed"); return; }
        if (Game1.player.CurrentTool != Rod || Game1.player.TilePoint != origin) { Finish("stopped", "player_took_control"); return; }
        if (OtherController()) { Finish("stopped", "other_controller_active"); return; }
        var elapsed = (DateTime.UtcNow - started).TotalSeconds;
        if (elapsed >= limits.MaxSeconds || Game1.timeOfDay >= limits.StopAt || Game1.player.Stamina < 10)
        { Finish("stopped", elapsed >= limits.MaxSeconds ? "time_limit" : "energy_or_bedtime"); return; }
        if (Game1.eventUp || Game1.fadeToBlack) { Finish("stopped", "event_or_transition"); return; }
        var rod = Rod!;
        if (Game1.activeClickableMenu is BobberBar)
        {
            sawBar = true;
            phase = "minigame";
            if ((DateTime.UtcNow - castStarted).TotalSeconds > 120) Finish("blocked", "minigame_timeout");
            return;
        }
        if (Game1.activeClickableMenu != null) { Finish("blocked", "menu_requires_player:" + Game1.activeClickableMenu.GetType().Name); return; }
        if (Game1.paused) { phase = "paused"; return; }
        if (landing)
        {
            RecordCatch();
            if (rod.inUse() || Game1.player.UsingTool) return;
            EndCast();
        }
        if (rod.fishCaught)
        {
            phase = "landing";
            beforeCatch = Inventory();
            rod.doneHoldingFish(Game1.player); // 游戏原生收获入口，包含正常奖励与背包检查。
            landing = true;
            RecordCatch();
            return;
        }
        if (FishingPolicy.CanHook(rod.isNibbling, hookSent, rod.hit, rod.pullingOutOfWater, rod.isReeling))
        {
            phase = "hooking";
            hookSent = true;
            rod.DoFunction(Game1.currentLocation, (int)rod.bobber.X, (int)rod.bobber.Y, 1, Game1.player);
            return;
        }
        if (rod.isFishing)
        {
            phase = "waiting_bite";
            sawWater = true;
            if ((DateTime.UtcNow - castStarted).TotalSeconds > 75) { Finish("blocked", "bite_timeout"); }
            return;
        }
        if (inCast)
        {
            if (!rod.inUse() && !Game1.player.UsingTool && !rod.pullingOutOfWater && DateTime.UtcNow > castStarted.AddSeconds(3)) EndCast();
            else if ((DateTime.UtcNow - castStarted).TotalSeconds > 120) Finish("blocked", "cast_stalled");
            return;
        }
        if (DateTime.UtcNow < nextCast) return;
        var end = FishingPolicy.EndReason(casts, fish, failures, elapsed, Game1.timeOfDay, Game1.player.Stamina, EmptySlots(), limits);
        if (end != null)
        {
            var completed = end == "target_fish_reached" || (end == "cast_limit" && limits.TargetFish == 0);
            Finish(completed ? "completed" : "stopped", end);
            return;
        }
        if (!Context.IsPlayerFree) { Finish("blocked", "player_not_free"); return; }
        if (ChooseFacing(facing) < 0) { Finish("blocked", "water_target_changed"); return; }
        Game1.player.faceDirection(facing);
        casts++;
        castStarted = DateTime.UtcNow;
        inCast = true;
        sawWater = sawBar = hookSent = catchInCast = false;
        phase = "casting";
        Game1.player.BeginUsingTool();
        // 选择固定抛竿力度，再走游戏原生动画、落点与资源消耗流程。
        rod.castingPower = 0.85f;
        Helper.Reflection.GetMethod(rod, "startCasting").Invoke();
        Log("cast_started");
    }

    private Dictionary<string, int> Inventory() => Game1.player.Items.Where(i => i != null)
        .GroupBy(i => $"{i.QualifiedItemId}|{i.Quality}").ToDictionary(g => g.Key, g => g.Sum(i => i.Stack));

    private void RecordCatch()
    {
        var gained = new List<object>();
        var any = false;
        foreach (var group in Game1.player.Items.Where(i => i != null).GroupBy(i => $"{i.QualifiedItemId}|{i.Quality}"))
        {
            var amount = group.Sum(i => i.Stack) - beforeCatch.GetValueOrDefault(group.Key);
            if (amount <= 0) continue;
            var item = group.First();
            var isFish = item.Category == -4 || item.HasContextTag("counts_as_fish_catch");
            if (isFish) fish += amount; else trash += amount;
            gained.Add(new { id = item.QualifiedItemId, name = item.DisplayName, quality = item.Quality, count = amount, isFish });
            any = true;
        }
        if (any)
        {
            catchInCast = true;
            catches.Add(new { cast = casts, items = gained });
            failures = 0;
            beforeCatch = Inventory();
            Log("catch_verified", gained);
        }
    }

    private void EndCast()
    {
        var got = catchInCast;
        if (!got) failures++;
        if (!sawWater && !sawBar && !got) emptyCasts++; else emptyCasts = 0;
        Log(got ? "cast_done" : "cast_no_catch");
        inCast = landing = false;
        nextCast = DateTime.UtcNow.AddMilliseconds(600);
        phase = "ready";
        if (emptyCasts >= 2) Finish("blocked", "same_shore_two_empty_casts");
    }

    private void Finish(string outcome, string why)
    {
        if (!Active) return;
        Active = false; // 先交还小游戏输入，后处理取消。
        state = outcome;
        reason = why;
        phase = "idle";
        try
        {
            if (Context.IsWorldReady && Game1.currentLocation == location && Game1.player.CurrentTool == Rod)
            {
                if (Game1.activeClickableMenu is BobberBar bar) bar.emergencyShutDown();
                else if (inCast && !Rod!.fishCaught && !Rod.showingTreasure) Rod.doneFishing(Game1.player);
                Rod!.recastTimerMs = 0;
            }
            Log("finished");
        }
        finally { WriteStatus(); }
    }

    private void Log(string result, object? detail = null)
    {
        var line = JsonSerializer.Serialize(new {
            observedUtc = DateTime.UtcNow, runId, cast = casts, location = location?.NameOrUniqueName,
            tile = new { x = origin.X, y = origin.Y }, facing, result, state, phase, reason, fish, trash, detail
        }, json) + Environment.NewLine;
        AppendLine(Path.Combine(runtime, "history.jsonl"), line);
        Monitor.Log($"[钓鱼 {runId} #{casts}] {result}，鱼 {fish}，其他 {trash}，{reason}", LogLevel.Info);
    }

    private void WriteStatus()
    {
        var path = Path.Combine(runtime, "status.json");
        var ready = Context.IsWorldReady;
        var text = JsonSerializer.Serialize(new {
            version = 1, capabilities = new[] { "start", "stop_run", "key" },
            startKey = StartKey.ToString(), stopKey = StopKey.ToString(),
            mode = "game", observedUtc = DateTime.UtcNow, state, active = Active, phase, runId, reason, triggeredBy,
            casts, fish, trash, failures, catches, ack, worldReady = ready,
            location = ready ? Game1.currentLocation.NameOrUniqueName : null,
            energy = ready ? Game1.player.Stamina : (float?)null,
            time = ready ? Game1.timeOfDay : (int?)null,
            playerBusy = ready && Game1.player.UsingTool,
            automaticPaused = ready && pausedDate == DateKey(),
            menu = ready ? Game1.activeClickableMenu?.GetType().Name : null,
            limits
        }, json);
        if (TryReplace(path, text)) return;
        // 兜底：直接覆盖写（读方用 FileShare 兼容），失败只记日志，绝不抛异常。
        try
        {
            using var stream = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
            using var writer = new StreamWriter(stream);
            writer.Write(text);
        }
        catch (Exception ex)
        {
            Monitor.Log($"写状态失败：{ex.GetType().Name}", LogLevel.Trace);
        }
    }

    /// <summary>把文本原子替换到目标文件；目标被其他进程占用时短暂重试，失败返回 false。</summary>
    private bool TryReplace(string path, string text)
    {
        var tmp = path + ".tmp";
        try
        {
            using (var stream = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete))
            using (var writer = new StreamWriter(stream)) writer.Write(text);
            for (var attempt = 0; attempt < 4; attempt++)
            {
                try { File.Move(tmp, path, true); return true; }
                catch (IOException) { Thread.Sleep(15); }
                catch (UnauthorizedAccessException) { Thread.Sleep(15); }
            }
        }
        catch (Exception)
        {
            // 落盘失败交给调用方兜底
        }
        TryDelete(tmp);
        return false;
    }

    /// <summary>追加一行历史；失败不抛出（历史写不进去不中断任务）。</summary>
    private void AppendLine(string path, string line)
    {
        for (var attempt = 0; attempt < 3; attempt++)
        {
            try
            {
                using var stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
                using var writer = new StreamWriter(stream);
                writer.Write(line);
                return;
            }
            catch (IOException) { Thread.Sleep(15); }
            catch (UnauthorizedAccessException) { Thread.Sleep(15); }
        }
    }

    private static bool TryMove(string from, string to)
    {
        for (var attempt = 0; attempt < 6; attempt++)
        {
            try { File.Move(from, to, true); return true; }
            catch (IOException) { Thread.Sleep(20); }
            catch (UnauthorizedAccessException) { Thread.Sleep(20); }
        }
        return false;
    }

    private static void TryDelete(string path)
    {
        try { File.Delete(path); }
        catch (Exception) { }
    }

    private sealed class Command
    {
        public string Id { get; set; } = "";
        public string Action { get; set; } = "";
        public DateTime IssuedAt { get; set; }
        public string RunId { get; set; } = "";
        public List<string> Excluded { get; set; } = new();
        public Limits Limits { get; set; } = new();
    }
}

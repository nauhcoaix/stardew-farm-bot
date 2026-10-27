using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using System.Text.Json;

namespace AgentStardew;

public sealed class ModEntry : Mod
{
    private BridgeServer? server;
    private readonly ActionCoordinator actions = new();
    private readonly Observations observations = new();
    private readonly BackgroundUpdates background = new();
    private BridgeServer.Pending? screenshot;
    // 游戏窗口左上角的叠加层：当前要做的动作 + 机器人记住的内容（由 Python 端推送）。
    private string hudTitle = "";
    private string[] hudLines = Array.Empty<string>();
    private DateTime hudUntil = DateTime.MinValue;
    private bool hudDrawn = false;
    private bool hudCalled = false;
    public override void Entry(IModHelper helper)
    {
        try { server = new BridgeServer(17654); }
        catch (Exception error) { Monitor.Log($"启动本机桥接失败：{error.Message}", LogLevel.Error); return; }
        actions.OnError = error => Monitor.Log($"游戏动作失败：{error}", LogLevel.Error);
        helper.Events.GameLoop.GameLaunched += (_, _) => background.Initialize();
        helper.Events.GameLoop.UpdateTicking += OnTick;
        helper.Events.GameLoop.UpdateTicked += (_, _) =>
        {
            background.AfterUpdate();
            if (Context.IsWorldReady) observations.RecordPosition();
        };
        helper.Events.GameLoop.SaveLoaded += (_, _) => Reset();
        helper.Events.GameLoop.ReturnedToTitle += (_, _) => Reset();
        helper.Events.Display.Rendered += OnRendered;
        helper.Events.Display.RenderedHud += (_, e) => DrawHud(e.SpriteBatch);
        Monitor.Log("Agent 桥接已监听 ws://127.0.0.1:17654/，支持游戏失焦时继续运行。", LogLevel.Info);
    }
    private void Reset()
    {
        actions.Reset(); observations.Reset(); server?.InvalidateSessions();
        if (screenshot is { } pending) Send(pending, Response.Fail(pending.Request, "STALE_REF", "存档会话已改变。"));
        screenshot = null;
    }
    private static void Send(BridgeServer.Pending pending, Response response) => _ = pending.Peer.Send(response);
    private void OnTick(object? sender, UpdateTickingEventArgs e)
    {
        if (server == null) return;
        background.BeforeUpdate();
        if (!Context.IsWorldReady) actions.Stop("STALE_REF", "存档未加载。");
        actions.Tick(DateTime.UtcNow, owner => ((BridgeServer.Peer)owner).Lifetime.IsCancellationRequested);
        if (screenshot is { } shot && (shot.Peer.Lifetime.IsCancellationRequested || DateTime.UtcNow >= shot.Received.AddMilliseconds(shot.Request.TimeoutMs)))
        { Send(shot, Response.Fail(shot.Request, "TIMEOUT", "截图等待绘制超时。")); screenshot = null; }
        for (var i = 0; i < 16 && server.Inbox.TryDequeue(out var pending); i++)
        {
            if (pending.Peer.Lifetime.IsCancellationRequested) continue;
            var r = pending.Request;
            try
            {
                Protocol.Validate(r);
                if (DateTime.UtcNow >= pending.Received.AddMilliseconds(r.TimeoutMs)) throw new ActionFault("TIMEOUT", "请求在队列中已过期。");
                if (r.Command == "hello")
                {
                    pending.Peer.Handshaken = true;
                    Send(pending, Response.Ok(r, new { mode = "game", kind = "hello", instanceId = observations.InstanceId, modVersion = ModManifest.Version.ToString(), gameVersion = Game1.GetVersionString(), smapiVersion = Constants.ApiVersion.ToString(), saveLoaded = Context.IsWorldReady, singlePlayer = !Context.IsMultiplayer, capabilities = Protocol.Capabilities }));
                    continue;
                }
                if (!pending.Peer.Handshaken) throw new ActionFault("INCOMPATIBLE_PROTOCOL", "需要先发送 hello。");
                if (r.Command == "status") { Send(pending, Response.Ok(r, actions.Status)); continue; }
                if (r.Command == "hud")
                {
                    SetHud(r.Args);
                    Send(pending, Response.Ok(r, new { kind = "hud", lines = hudLines.Length, untilUtc = hudUntil }));
                    continue;
                }
                if (r.Command == "stop")
                {
                    var id = r.Args.TryGetProperty("actionId", out _) ? Protocol.Text(r.Args, "actionId") : null;
                    if (id == null || id == actions.ActiveId) actions.Stop();
                    Send(pending, Response.Ok(r, actions.Status)); continue;
                }
                if (!Context.IsWorldReady) throw new ActionFault("NO_SAVE_LOADED", "请先加载单人存档。");
                if (Context.IsMultiplayer) throw new ActionFault("UNSUPPORTED", "当前只支持单人存档。");
                if (r.Command == "snapshot")
                {
                    Send(pending, Response.Ok(r, observations.Snapshot(r.Args.TryGetProperty("radius", out _) ? Protocol.Integer(r.Args, "radius") : 8))); continue;
                }
                if (r.Command == "menuclick")
                {
                    var mx = Protocol.Integer(r.Args, "x");
                    var my = Protocol.Integer(r.Args, "y");
                    Send(pending, Response.Ok(r, MenuDump.Click(mx, my))); continue;
                }
                if (r.Command == "ship")
                {
                    Send(pending, Response.Ok(r, MenuDump.Ship(Protocol.Integer(r.Args, "slot")))); continue;
                }
                if (r.Command == "chestget")
                {
                    Send(pending, Response.Ok(r, MenuDump.ChestGet(Protocol.Integer(r.Args, "x"), Protocol.Integer(r.Args, "y"), Protocol.Text(r.Args, "name")))); continue;
                }
                if (r.Command == "warp")
                {
                    Send(pending, Response.Ok(r, MenuDump.Warp(Protocol.Text(r.Args, "location"), Protocol.Integer(r.Args, "x"), Protocol.Integer(r.Args, "y")))); continue;
                }
                if (r.Command == "forceunlock")
                {
                    Send(pending, Response.Ok(r, MenuDump.ForceUnlock())); continue;
                }
                if (r.Command == "bin")
                {
                    Send(pending, Response.Ok(r, MenuDump.Bin())); continue;
                }
                if (r.Command == "chestput")
                {
                    Send(pending, Response.Ok(r, MenuDump.ChestPut(Protocol.Integer(r.Args, "x"), Protocol.Integer(r.Args, "y"), Protocol.Integer(r.Args, "slot")))); continue;
                }
                if (r.Command == "placechest")
                {
                    Send(pending, Response.Ok(r, MenuDump.PlaceChest(Protocol.Integer(r.Args, "x"), Protocol.Integer(r.Args, "y")))); continue;
                }
                if (r.Command == "shopbuy")
                {
                    Send(pending, Response.Ok(r, MenuDump.ShopBuy(Protocol.Integer(r.Args, "index"), r.Args.TryGetProperty("qty", out _) ? Protocol.Integer(r.Args, "qty") : 1))); continue;
                }
                if (r.Command == "menuclickinv")
                {
                    Send(pending, Response.Ok(r, MenuDump.InvClick(Protocol.Integer(r.Args, "x"), Protocol.Integer(r.Args, "y")))); continue;
                }
                if (r.Command == "menuclose")
                {
                    Send(pending, Response.Ok(r, MenuDump.Close())); continue;
                }
                if (r.Command == "menudump")
                {
                    Send(pending, Response.Ok(r, MenuDump.Dump())); continue;
                }
                if (r.Command == "reset")
                {
                    // 清空动作记录表（幂等保护用的），避免 20 万条后 OUTPUT_LIMIT 卡死
                    actions.Reset();
                    Send(pending, Response.Ok(r, new { kind = "reset", reset = true }));
                    continue;
                }
                if (r.Command == "screenshot")
                {
                    if (screenshot != null) throw new ActionFault("BUSY", "已有截图请求。");
                    screenshot = pending; continue;
                }
                actions.Start(r, pending.Peer, pending.Received.AddMilliseconds(r.TimeoutMs), () => GameActions.Create(r, observations, Helper.Reflection), response => Send(pending, response));
            }
            catch (ActionFault error) { Send(pending, Response.Fail(r, error.Code, error.Message)); }
            catch (Exception error) { Monitor.Log($"处理 {r.Command} 失败：{error}", LogLevel.Error); Send(pending, Response.Fail(r, "INTERNAL_ERROR", "游戏桥接异常，请查看 SMAPI 日志。")); }
        }
    }
    /// <summary>叠加层诊断：写到 Mod 自己的 runtime/hud-diag.log，便于排查画没画出来。</summary>
    private void HudDiag(string text)
    {
        try
        {
            var dir = Path.Combine(Helper.DirectoryPath, "runtime");
            Directory.CreateDirectory(dir);
            File.AppendAllText(Path.Combine(dir, "hud-diag.log"),
                $"{DateTime.UtcNow:O} {text}{Environment.NewLine}");
        }
        catch (Exception)
        {
            // 诊断写不进去不影响游戏
        }
    }

    /// <summary>收下 Python 推来的叠加层文字；超时自动消失，避免残留成“幽灵状态”。</summary>
    private void SetHud(JsonElement args)
    {
        var title = args.TryGetProperty("title", out var t) && t.ValueKind == JsonValueKind.String ? (t.GetString() ?? "") : "";
        var lines = new List<string>();
        if (args.TryGetProperty("lines", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in arr.EnumerateArray().Take(12))
            {
                if (item.ValueKind != JsonValueKind.String) continue;
                var text = (item.GetString() ?? "").Replace('\r', ' ').Replace('\n', ' ').Trim();
                if (text.Length == 0) continue;
                lines.Add(text.Length > 120 ? text[..120] : text);
            }
        }
        var ttl = 60000;
        if (args.TryGetProperty("ttlMs", out var ms) && ms.ValueKind == JsonValueKind.Number && ms.TryGetInt32(out var value))
            ttl = Math.Clamp(value, 1000, 600000);
        hudTitle = title.Length > 120 ? title[..120] : title;
        hudLines = lines.ToArray();
        hudUntil = DateTime.UtcNow.AddMilliseconds(ttl);
        HudDiag($"set title={hudTitle.Length} rows={hudLines.Length} until={hudUntil:O}");
    }

    /// <summary>只画文字层，不改游戏状态；绘制异常就让叠加层自己消失。</summary>
    private void DrawHud(SpriteBatch batch)
    {
        if (!hudCalled) { hudCalled = true; HudDiag($"draw called until={hudUntil:O} rows={hudLines.Length}"); }
        if (hudUntil == DateTime.MinValue || DateTime.UtcNow >= hudUntil) return;
        if (hudTitle.Length == 0 && hudLines.Length == 0) return;
        try
        {
            // RenderedHud 里 SpriteBatch 已经 Begin 过了：这里只 Draw，不能再 Begin/End。
            var font = Game1.smallFont;
            var lineHeight = font.MeasureString("Ag").Y + 4f;
            var width = hudTitle.Length > 0 ? font.MeasureString(hudTitle).X : 0f;
            foreach (var line in hudLines) width = Math.Max(width, font.MeasureString(line).X);
            var rows = hudLines.Length + (hudTitle.Length > 0 ? 1 : 0);
            // UI 视口原点常常是负数（画面被放大/居中），所以按视口原点换算到可见窗口左上角。
            var vp = Game1.uiViewport;
            var left = (vp.Width > 0 ? 12 - vp.X : 12);
            var top = (vp.Height > 0 ? 12 - vp.Y : 12);
            var box = new Rectangle(left, top, (int)width + 24, (int)(lineHeight * rows) + 16);
            batch.Draw(Game1.staminaRect, box, Color.Black * 0.62f);
            batch.Draw(Game1.staminaRect, new Rectangle(box.X, box.Y, 4, box.Height), Color.LimeGreen * 0.9f);
            var y = box.Y + 8f;
            if (hudTitle.Length > 0)
            {
                batch.DrawString(font, hudTitle, new Vector2(box.X + 12, y), Color.Gold);
                y += lineHeight;
            }
            foreach (var line in hudLines)
            {
                batch.DrawString(font, line, new Vector2(box.X + 12, y), Color.White);
                y += lineHeight;
            }
            if (!hudDrawn)
            {
                hudDrawn = true;
                HudDiag($"drawn at {box.X},{box.Y} size {box.Width}x{box.Height} viewport {Game1.uiViewport.X},{Game1.uiViewport.Y} {Game1.uiViewport.Width}x{Game1.uiViewport.Height}");
                Monitor.Log($"叠加层已显示（{hudLines.Length + 1} 行）", LogLevel.Info);
            }
        }
        catch (Exception error)
        {
            hudUntil = DateTime.MinValue;
            HudDiag($"error {error.GetType().Name}: {error.Message}");
            Monitor.Log($"叠加层绘制失败，已关闭：{error.GetType().Name}: {error.Message}", LogLevel.Warn);
        }
    }

    private void OnRendered(object? sender, RenderedEventArgs e)
    {
        if (screenshot is not { } pending) return;
        screenshot = null;
        try
        {
            var device = Game1.graphics.GraphicsDevice;
            var width = device.PresentationParameters.BackBufferWidth; var height = device.PresentationParameters.BackBufferHeight;
            if ((long)width * height > 8388608) throw new ActionFault("OUTPUT_LIMIT", "画面尺寸过大，请降低窗口分辨率。");
            var colors = new Color[width * height]; device.GetBackBufferData(colors);
            using var texture = new Texture2D(device, width, height); texture.SetData(colors);
            using var bytes = new MemoryStream(); texture.SaveAsPng(bytes, width, height);
            if (bytes.Length > 2800000) throw new ActionFault("OUTPUT_LIMIT", "PNG 超出协议大小，请降低窗口分辨率。");
            Send(pending, Response.Ok(pending.Request, new { kind = "screenshot", mimeType = "image/png", base64 = Convert.ToBase64String(bytes.ToArray()) }));
        }
        catch (Exception error) { Send(pending, Response.Fail(pending.Request, error is ActionFault fault ? fault.Code : "INTERNAL_ERROR", $"截图失败：{error.Message}")); }
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing) { actions.Stop(); background.Dispose(); server?.Dispose(); }
        base.Dispose(disposing);
    }
}

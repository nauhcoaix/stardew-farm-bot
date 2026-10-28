using System.Reflection;
using StardewModdingAPI;
using StardewValley;

namespace AgentStardew;

// 交互/菜单关闭后，游戏偶尔不会把 Farmer.CanMove 复位；
// 这里只在“没有任何明确阻塞原因”时恢复，避免打断工具动画、事件、小游戏与切图。
internal static class PlayerRecovery
{
    private static readonly MethodInfo? ForceCanMove = typeof(Farmer).GetMethod(
        "forceCanMove", BindingFlags.Public | BindingFlags.Instance
    );

    //: 连续两帧都是“无理由锁定”才恢复，避开菜单关闭/动画收尾的那一帧。
    private static int staleTicks;
    public static int Recovered { get; private set; }

    public static string? LockReason()
    {
        if (!Context.IsWorldReady || Game1.player is not { } player) return "no-world";
        if (Game1.activeClickableMenu != null) return "menu";
        if (Game1.eventUp) return "event";
        if (Game1.fadeToBlack) return "fade";
        if (Game1.newDay) return "new-day";
        if (Game1.currentMinigame != null) return "minigame";
        if (player.UsingTool) return "tool";
        if (player.isEating) return "eating";
        if (player.isInBed.Value) return "in-bed";
        return player.CanMove ? null : "stale";
    }

    /// <summary>若 CanMove 是“卡住”而不是合法阻塞，则恢复；返回是否恢复过。</summary>
    public static bool TryRecover()
    {
        if (!Context.IsWorldReady || Game1.player is not { } player) return false;
        if (player.CanMove) { staleTicks = 0; return false; }
        if (LockReason() != "stale")
        {
            staleTicks = 0;
            return false;
        }
        if (++staleTicks < 2) return false;
        staleTicks = 0;
        try { ForceCanMove?.Invoke(player, null); }
        catch { /* 游戏版本没有该方法时退回直接赋值 */ }
        player.CanMove = true;
        Recovered++;
        return true;
    }
}

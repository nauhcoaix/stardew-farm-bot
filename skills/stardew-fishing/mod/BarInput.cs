using Microsoft.Xna.Framework.Input;
using StardewValley;
using StardewValley.Menus;

namespace StandaloneFishingSkill;

// 仅在本技能拥有的 BobberBar.update 内模拟工具键；结束/异常时原样恢复输入。
internal static class BarInput
{
    private static BobberBar? previous;
    private static float lastFish;
    private static bool held;

    internal sealed record Saved(MouseState Mouse, KeyboardState Keyboard, GamePadState Pad);

    public static void Prefix(BobberBar __instance, out Saved? __state)
    {
        __state = null;
        var owner = ModEntry.Instance;
        if (owner?.Active != true || Game1.player.CurrentTool != owner.Rod
            || !ReferenceEquals(Game1.activeClickableMenu, __instance) || Game1.paused) return;
        if (previous != __instance) { previous = __instance; lastFish = __instance.bobberPosition; held = false; }
        held = FishingPolicy.Hold(__instance.bobberPosition, __instance.bobberPosition - lastFish,
            __instance.bobberBarPos, __instance.bobberBarSpeed, __instance.bobberBarHeight, held);
        lastFish = __instance.bobberPosition;
        __state = new Saved(Game1.oldMouseState, Game1.oldKBState, Game1.oldPadState);
        var mouse = Game1.oldMouseState;
        Game1.oldMouseState = new MouseState(mouse.X, mouse.Y, mouse.ScrollWheelValue,
            held ? ButtonState.Pressed : ButtonState.Released, ButtonState.Released, ButtonState.Released,
            ButtonState.Released, ButtonState.Released);
        Game1.oldKBState = new KeyboardState();
        Game1.oldPadState = new GamePadState();
    }

    public static Exception? Finalizer(Exception? __exception, Saved? __state)
    {
        if (__state != null)
        {
            Game1.oldMouseState = __state.Mouse;
            Game1.oldKBState = __state.Keyboard;
            Game1.oldPadState = __state.Pad;
        }
        return __exception;
    }
}

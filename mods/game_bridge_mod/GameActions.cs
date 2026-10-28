using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Objects;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
using StardewValley.Util;

namespace AgentStardew;

public static class GameActions
{
    private sealed class Immediate : IGameAction
    {
        private readonly Func<object> result;
        public Immediate(Func<object> result) => this.result = result;
        public object Tick() => result();
        public void Cancel() { }
    }
    public static IGameAction Create(Request request, Observations observations, IReflectionHelper reflection)
    {
        if (request.Command == "menu") return new MenuAction(observations, observations.Resolve(Protocol.Text(request.Args, "target"), true));
        if (!Game1.player.CanMove || Game1.player.UsingTool || Game1.activeClickableMenu != null || Game1.eventUp || Game1.fadeToBlack)
            throw new ActionFault("BUSY", "角色处于动画、菜单或场景过渡中，请先观察。");
        switch (request.Command)
        {
            case "select":
                var slot = Protocol.Integer(request.Args, "slot");
                if (slot < 0 || slot >= Game1.player.Items.Count) throw new ActionFault("INVALID_ARGUMENT", "槽位超出当前背包范围。");
                var changed = Game1.player.CurrentToolIndex != slot;
                Game1.player.CurrentToolIndex = slot;
                return new Immediate(() => observations.Result($"已选择槽位 {slot}。", changed));
            case "move":
                Observations.Target? target = null;
                Point tile;
                if (request.Args.TryGetProperty("near", out _)) { target = observations.Resolve(Protocol.Text(request.Args, "near")); tile = target.Tile; }
                else { var raw = request.Args.GetProperty("tile"); tile = new(Protocol.Integer(raw, "x"), Protocol.Integer(raw, "y")); }
                return new MoveAction(observations, reflection, tile, target, request.Args.TryGetProperty("near", out var near) ? near.GetString() : null);
            case "use": return new UseAction(observations, observations.Resolve(Protocol.Text(request.Args, "target")));
            case "interact": return new InteractAction(observations, observations.Resolve(Protocol.Text(request.Args, "target")));
            default: throw new ActionFault("UNSUPPORTED", "不支持该动作。");
        }
    }
    private static void Reach(Point target)
    {
        var current = Observations.Position;
        if (Math.Abs(current.X - target.X) + Math.Abs(current.Y - target.Y) > 1) throw new ActionFault("OUT_OF_REACH", "需要先移动到目标相邻格子。");
        var dx = target.X - current.X; var dy = target.Y - current.Y;
        if (dx != 0 || dy != 0) Game1.player.faceDirection(dx > 0 ? 1 : dx < 0 ? 3 : dy > 0 ? 2 : 0);
    }
    private sealed class MoveAction : IGameAction
    {
        private readonly Observations observations;
        private readonly MovementInput input = new();
        private readonly IReflectedField<IInputSimulator?> simulator;
        private readonly Farmer player = Game1.player;
        private readonly GameLocation location = Game1.currentLocation;
        private readonly string? reference;
        private readonly bool exitGoal;
        private const int MaxReplans = 3;
        private const double StallSeconds = 1.2;
        private const double WaitForCharacterSeconds = 1.5;
        private const float WaypointTolerance = 12f;
        private bool nearGoal;
        private Point goal;
        private Queue<Point> path;
        private Vector2 lastPosition = Game1.player.Position;
        private DateTime lastMoved = DateTime.UtcNow;
        private DateTime blockedSince = DateTime.MinValue;
        private int replans;
        private bool moved;

        public MoveAction(Observations observations, IReflectionHelper reflection, Point tile, Observations.Target? target, string? reference)
        {
            this.observations = observations; this.reference = reference;
            exitGoal = target?.Identity is Warp || location.warps.Any(w => w.X == tile.X && w.Y == tile.Y);
            nearGoal = target != null && !exitGoal;
            goal = tile;
            path = BuildPath();
            simulator = reflection.GetField<IInputSimulator?>(typeof(Game1), "inputSimulator");
            if (simulator.GetValue() != null) throw new ActionFault("BUSY", "其他控制器正在使用游戏模拟输入。");
            simulator.SetValue(input);
        }

        //: 用 A* 找路（4 邻接、曼哈顿启发）；找不到就明确报“不可达”，不原地打转。
        private Queue<Point> BuildPath()
        {
            if (!Observations.InBounds(location, goal) && !exitGoal)
                throw new ActionFault("INVALID_ARGUMENT", "坐标超出地图范围。");
            var nodes = Pathfinder.Find(location, Observations.Position, goal, nearGoal, exitGoal);
            if (nodes == null)
                throw new ActionFault(
                    "PATH_BLOCKED",
                    "A* 找不到可行路径：目标可能被家具/角色挡住，或需要先经过门/楼梯。换目标，或先 move_to 到中间点。"
                );
            return new Queue<Point>(nodes);
        }

        private bool TryReplan()
        {
            if (replans >= MaxReplans) return false;
            var nodes = Pathfinder.Find(location, Observations.Position, goal, nearGoal, exitGoal);
            if (nodes == null) return false;
            path = new Queue<Point>(nodes);
            replans++;
            lastMoved = DateTime.UtcNow;
            blockedSince = DateTime.MinValue;
            return true;
        }

        private bool BlockedByCharacter(Point tile)
        {
            foreach (var npc in location.characters)
                if (npc.TilePoint == tile) return true;
            return false;
        }

        public object? Tick()
        {
            input.Direction = -1;
            if (!ReferenceEquals(simulator.GetValue(), input)) throw new ActionFault("BUSY", "游戏模拟输入已被其他控制器接管。");
            if (!ReferenceEquals(location, Game1.currentLocation)) return observations.Result("移动触发场景切换，请重新观察。");
            if (reference != null)
            {
                var current = observations.Resolve(reference).Tile;
                if (current != goal)
                {
                    goal = current;
                    nearGoal = true;
                    if (!TryReplan()) throw new ActionFault("PATH_BLOCKED", "目标移动后 A* 找不到新路径。");
                }
            }
            if (Game1.activeClickableMenu != null) return observations.Result("移动触发菜单，请根据新快照选择。", moved);
            if (Game1.fadeToBlack || !Game1.player.CanMove) return null;

            var now = DateTime.UtcNow;
            if (Game1.player.Position != lastPosition)
            {
                moved = true;
                lastPosition = Game1.player.Position;
                lastMoved = now;
                blockedSince = DateTime.MinValue;
            }
            else if ((now - lastMoved).TotalSeconds > StallSeconds)
            {
                if (!TryReplan())
                    throw new ActionFault(
                        "PATH_BLOCKED",
                        $"连续 {StallSeconds:0.0} 秒没有前进，A* 重算 {replans} 次仍未脱困。"
                    );
            }

            var center = Game1.player.GetBoundingBox().Center.ToVector2();
            while (path.Count > 0 && Vector2.Distance(center, path.Peek().ToVector2() * 64 + new Vector2(32)) <= WaypointTolerance)
                path.Dequeue();
            if (path.Count == 0) return observations.Result("已到达目标（A* 重算 " + replans + " 次）。", moved);

            var next = path.Peek();
            if (!(exitGoal && next == goal) && !Observations.Walkable(location, next))
            {
                // 被 NPC/宠物临时挡住：先等一会儿，别把路直接判死；仍不通再重算。
                if (BlockedByCharacter(next))
                {
                    if (blockedSince == DateTime.MinValue) blockedSince = now;
                    else if ((now - blockedSince).TotalSeconds < WaitForCharacterSeconds) return null;
                    blockedSince = DateTime.MinValue;
                }
                if (!TryReplan())
                    throw new ActionFault("PATH_BLOCKED", "路径被新的障碍挡住，A* 重算后仍不可达。");
                return null;
            }

            var delta = next.ToVector2() * 64 + new Vector2(32) - center;
            input.Direction = Math.Abs(delta.X) > Math.Abs(delta.Y)
                ? delta.X > 0 ? 1 : 3
                : delta.Y > 0 ? 2 : 0;
            return null;
        }

        public void Cancel()
        {
            input.Direction = -1;
            if (ReferenceEquals(simulator.GetValue(), input)) simulator.SetValue(null);
            player.Halt();
        }
    }

        /// <summary>镰刀在游戏里是 MeleeWeapon（47 普通镰刀 / 53 金镰刀），不是普通 Tool。</summary>
        private static bool IsScythe(Tool tool)
        {
            if (tool is not MeleeWeapon) return false;
            var name = tool.GetType().Name ?? "";
            if (name.Contains("Scythe", StringComparison.OrdinalIgnoreCase)) return true;
            var id = tool.ItemId ?? "";
            return id is "47" or "53" || id.EndsWith(")47", StringComparison.Ordinal) || id.EndsWith(")53", StringComparison.Ordinal);
        }
    private sealed class UseAction : IGameAction
    {
        private readonly Observations observations;
        private readonly Observations.Target target;
        private readonly string operation;
        private readonly string before;
        private readonly WateringCan? refillCan;
        private readonly int waterBefore;
        private int ticks;
        private bool ended;
        private static string State(Point tile)
        {
            Game1.currentLocation.terrainFeatures.TryGetValue(tile.ToVector2(), out var terrain);
            Game1.currentLocation.objects.TryGetValue(tile.ToVector2(), out var obj);
            // 树要好几下才倒：把 health/树桩/倒下 也算进状态，否则每一下都被判"没变化"
            var tree = terrain as Tree;
            var extra = tree == null ? "-" : $"h{tree.health.Value}{tree.stump.Value}{tree.falling.Value}";
            return $"{terrain?.GetType().Name}/{(terrain as HoeDirt)?.state.Value}/{(terrain as HoeDirt)?.crop?.indexOfHarvest.Value}/{obj?.QualifiedItemId}/{extra}";
        }
        public UseAction(Observations observations, Observations.Target target)
        {
            this.observations = observations; this.target = target; Reach(target.Tile); before = State(target.Tile);
            var player = Game1.player;
            var selected = player.CurrentItem;
            target.Location.terrainFeatures.TryGetValue(target.Tile.ToVector2(), out var feature);
            var dirt = feature as HoeDirt;
            if (selected is StardewValley.Object seeds && seeds.Category == StardewValley.Object.SeedsCategory)
            {
                operation = "播种";
                if (seeds.Stack < 1) throw new ActionFault("INSUFFICIENT_RESOURCE", "种子不足。");
                if (dirt == null || dirt.crop != null) throw new ActionFault("INVALID_ARGUMENT", "播种需要没有作物的耕地。");
                // 与游戏的物品放置入口一致，由游戏检查季节、物品并扣除数量。
                if (!Utility.tryToPlaceItem(target.Location, seeds, target.Tile.X * 64, target.Tile.Y * 64)) throw new ActionFault("INSUFFICIENT_RESOURCE", "当前种子不能种在这里，请检查季节和土地。");
            }
            else if (selected is Tool tool && (tool is Hoe || tool is WateringCan || tool is Axe || tool is Pickaxe || IsScythe(tool)))
            {
                operation = tool is Hoe ? "耕地" : tool is WateringCan ? "浇水" : IsScythe(tool) ? "割草" : "使用工具";
                var refill = tool is WateringCan && target.Location.CanRefillWateringCanOnTile(target.Tile.X, target.Tile.Y);
                if (!refill && player.Stamina < 2) throw new ActionFault("INSUFFICIENT_RESOURCE", "体力不足。");
                if (tool is Hoe && (feature != null || target.Location.objects.ContainsKey(target.Tile.ToVector2()) || target.Location.doesTileHaveProperty(target.Tile.X, target.Tile.Y, "Diggable", "Back") == null)) throw new ActionFault("INVALID_ARGUMENT", "目标不是空置可耕地。");
                if (tool is WateringCan can)
                {
                    if (refill)
                    {
                        if (can.WaterLeft >= can.waterCanMax) throw new ActionFault("INVALID_ARGUMENT", "浇水壶已满。");
                        operation = "补水"; refillCan = can; waterBefore = can.WaterLeft;
                    }
                    else
                    {
                        if (can.WaterLeft <= 0) throw new ActionFault("INSUFFICIENT_RESOURCE", "浇水壶已空，请朝可补水的水源使用水壶。");
                        if (dirt == null) throw new ActionFault("INVALID_ARGUMENT", "请选择需要浇水的耕地或可补水的水源。");
                        if (dirt.state.Value == HoeDirt.watered) throw new ActionFault("INVALID_ARGUMENT", "目标已经浇水。");
                    }
                }
                player.lastClick = target.Tile.ToVector2() * 64 + new Vector2(32);
                player.toolPower.Value = 0;
                player.BeginUsingTool();
            }
            else throw new ActionFault("UNSUPPORTED", "请选择锄头、浇水壶、斧头、镐、镰刀或种子。");
        }
        public object? Tick()
        {
            if (!ReferenceEquals(target.Location, Game1.currentLocation)) throw new ActionFault("STALE_REF", "执行期间地图已改变。");
            ticks++;
            // 松开单次工具使用，避免升级工具进入蓄力；动画和资源消耗由游戏完成。
            if (!ended && ticks > 1 && Game1.player.UsingTool) { Game1.player.EndUsingTool(); ended = true; }
            if (ticks < 3 || Game1.player.UsingTool) return null;
            var changed = refillCan != null ? refillCan.WaterLeft > waterBefore : before != State(target.Tile);
            if (!changed) throw new ActionFault("UNSUPPORTED", "工具动画结束，但目标未发生可验证变化，请重新观察。");
            if (refillCan != null) return observations.Result($"补水完成，水量 {waterBefore} → {refillCan.WaterLeft}/{refillCan.waterCanMax}。");
            return observations.Result($"{operation}完成，已检查目标状态。");
        }
        public void Cancel() { /* 不回滚已触发的游戏动画或资源消耗。 */ }
    }
    private sealed class InteractAction : IGameAction
    {
        private readonly Observations observations;
        private readonly Observations.Target target;
        private readonly GameLocation location = Game1.currentLocation;
        private readonly object? priorMenu = Game1.activeClickableMenu;
        private readonly string priorMenuSignature = Observations.MenuSignature(Game1.activeClickableMenu);
        private readonly string priorInventory = InventoryState();
        private readonly bool targetExisted;
        private int ticks;
        private static string InventoryState() => string.Join("|", Game1.player.Items.Select(item => item == null ? "" : $"{item.QualifiedItemId}:{item.Stack}"));
        private bool TargetExists()
        {
            if (!ReferenceEquals(location, Game1.currentLocation)) return false;
            return target.Identity switch
            {
                Furniture furniture => location.furniture.Contains(furniture) && furniture.TileLocation.ToPoint() == target.Tile,
                StardewValley.Object obj => location.objects.TryGetValue(target.Tile.ToVector2(), out var current) && ReferenceEquals(current, obj),
                TerrainFeature feature => location.terrainFeatures.TryGetValue(target.Tile.ToVector2(), out var current) && ReferenceEquals(current, feature),
                NPC npc => location.characters.Contains(npc),
                Warp warp => location.warps.Contains(warp),
                _ => false
            };
        }
        public InteractAction(Observations observations, Observations.Target target)
        {
            this.observations = observations; this.target = target; targetExisted = TargetExists();
            if (target.Identity is BedFurniture bed)
            {
                var bounds = bed.boundingBox.Value;
                var player = Game1.player.GetBoundingBox();
                bounds.Inflate(64, 64);
                if (!bounds.Intersects(player)) throw new ActionFault("OUT_OF_REACH", "需要先靠近床。");
            }
            else Reach(target.Tile);
            if (!location.checkAction(new xTile.Dimensions.Location(target.Tile.X, target.Tile.Y), Game1.viewport, Game1.player))
                throw new ActionFault("UNSUPPORTED", "目标没有可用交互；自动出口可尝试移动到目标格子。");
        }
        public object? Tick()
        {
            if (++ticks < 3 || Game1.fadeToBlack) return null;
            var menuChanged = !ReferenceEquals(priorMenu, Game1.activeClickableMenu) || priorMenuSignature != Observations.MenuSignature(Game1.activeClickableMenu);
            var inventoryChanged = priorInventory != InventoryState();
            var targetDisappeared = targetExisted && !TargetExists();
            if (InteractionEvidence.Changed(!ReferenceEquals(location, Game1.currentLocation), menuChanged, inventoryChanged, targetDisappeared))
                return observations.Result("交互已产生可验证变化。");
            throw new ActionFault("UNSUPPORTED", "交互未产生当前支持的可验证结果，请重新观察。");
        }
        public void Cancel() { }
    }
    private sealed class MenuAction : IGameAction
    {
        private readonly Observations observations;
        private readonly Observations.Target target;
        private int ticks;
        private bool clicked;
        private readonly int day = Game1.dayOfMonth;
        private readonly bool sleep;
        public MenuAction(Observations observations, Observations.Target target)
        {
            this.observations = observations; this.target = target;
            if (target.Identity is not DialogueBox dialogue) throw new ActionFault("UNSUPPORTED", "当前仅支持对话及选项菜单。");
            sleep = Game1.currentLocation.lastQuestionKey == "Sleep" && target.Option >= 0 && dialogue.responses[target.Option].responseKey == "Yes";
        }
        public object? Tick()
        {
            if (++ticks < 3) return null;
            if (!clicked)
            {
                if (!ReferenceEquals(Game1.activeClickableMenu, target.Identity) || Observations.MenuSignature(Game1.activeClickableMenu) != target.Action) throw new ActionFault("MENU_MISMATCH", "菜单已改变。");
                var dialogue = (DialogueBox)target.Identity!;
                // 先正常展开对话，再选择已观察的选项；遵守菜单的防误触计时。
                if (target.Option >= 0) dialogue.selectedResponse = target.Option;
                dialogue.receiveLeftClick(dialogue.xPositionOnScreen + 8, dialogue.yPositionOnScreen + 8);
                clicked = !ReferenceEquals(Game1.activeClickableMenu, target.Identity) || Observations.MenuSignature(Game1.activeClickableMenu) != target.Action;
                return null;
            }
            if (sleep && Game1.dayOfMonth == day) return null;
            if (Game1.fadeToBlack || !Game1.player.CanMove && Game1.activeClickableMenu == null) return null;
            return observations.Result(sleep ? "睡觉完成，已确认日期推进。" : "菜单选择已生效。");
        }
        public void Cancel() { }
    }
}

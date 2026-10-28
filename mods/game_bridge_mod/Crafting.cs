using System;
using System.Collections.Generic;
using System.Linq;
using StardewValley;

namespace AgentStardew
{
    /// <summary>
    /// 制作物品。直接解析 Data/CraftingRecipes，不依赖 CraftingRecipe 的方法签名：
    /// 数据格式 = "材料id 数量 [材料id 数量 ...] / 解锁条件 / 产出id / 是否大件 / 显示名 / 描述"
    /// </summary>
    public static class Crafting
    {
        static Dictionary<string, string>? _data;

        static Dictionary<string, string> Data()
        {
            if (_data != null) return _data;
            try { _data = Game1.content.Load<Dictionary<string, string>>("Data/CraftingRecipes"); }
            catch { _data = new Dictionary<string, string>(); }
            return _data!;
        }

        static bool IdMatch(Item item, string id)
        {
            if (item == null) return false;
            var q = item.QualifiedItemId ?? "";
            return q == id || q == "(O)" + id || q.EndsWith(")" + id) || item.ItemId == id;
        }

        static int Count(IList<Item> inv, string id)
            => inv.Where(i => IdMatch(i, id)).Sum(i => i.Stack);

        static void Remove(IList<Item> inv, string id, int count)
        {
            for (int i = 0; i < inv.Count && count > 0; i++)
            {
                if (!IdMatch(inv[i], id)) continue;
                int take = Math.Min(count, inv[i].Stack);
                inv[i].Stack -= take;
                count -= take;
                if (inv[i].Stack <= 0) inv[i] = null!;
            }
        }

        static bool HasRoomFor(IList<Item> inv, Item sample)
        {
            foreach (var slot in inv) if (slot == null) return true;
            foreach (var slot in inv)
                if (slot != null && slot.canStackWith(sample) && slot.Stack < slot.maximumStackSize()) return true;
            return false;
        }

        static string? ResolveKey(string name)
        {
            var data = Data();
            if (data.ContainsKey(name)) return name;
            foreach (var key in data.Keys)
            {
                if (key.Equals(name, StringComparison.OrdinalIgnoreCase)) return key;
                try
                {
                    var r = new CraftingRecipe(key, false);
                    if (r.DisplayName == name || r.name == name) return key;
                }
                catch { }
            }
            return null;
        }

        public static object ListRecipes(int limit, bool learnedOnly)
        {
            var player = Game1.player;
            var data = Data();
            var outItems = new List<object>();
            foreach (var key in data.Keys)
            {
                bool learned = player != null && player.craftingRecipes.ContainsKey(key);
                if (learnedOnly && !learned) continue;
                var parts = data[key].Split('/');
                string display = key;
                try { display = new CraftingRecipe(key, false).DisplayName; } catch { }
                outItems.Add(new
                {
                    key,
                    display,
                    learned,
                    ingredients = parts.Length > 0 ? parts[0] : "",
                    output = parts.Length > 2 ? parts[2] : "",
                });
                if (outItems.Count >= Math.Max(1, limit)) break;
            }
            return new { kind = "recipes", count = outItems.Count, items = outItems };
        }

        public static object Craft(string name, int count)
        {
            var player = Game1.player;
            if (player == null) return new { kind = "craft", ok = false, reason = "玩家还没加载" };
            if (string.IsNullOrWhiteSpace(name)) return new { kind = "craft", ok = false, reason = "配方名不能为空" };

            var key = ResolveKey(name);
            if (key == null) return new { kind = "craft", ok = false, reason = "未知配方：" + name };
            if (!player.craftingRecipes.ContainsKey(key))
                return new { kind = "craft", ok = false, reason = "还没学会这个配方：" + name };

            var parts = Data()[key].Split('/');
            var ing = parts.Length > 0 ? parts[0] : "";
            var outId = parts.Length > 2 ? parts[2] : "";
            var display = parts.Length > 4 ? parts[4] : name;

            var need = new List<(string id, int n)>();
            var tokens = ing.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            for (int i = 0; i + 1 < tokens.Length; i += 2)
            {
                if (int.TryParse(tokens[i + 1], out var n)) need.Add((tokens[i], n));
            }
            if (need.Count == 0) return new { kind = "craft", ok = false, reason = "配方数据解析失败：" + ing };

            var inv = player.Items;
            var missing = new List<string>();
            foreach (var (id, n) in need)
            {
                int have = Count(inv, id);
                if (have < n)
                {
                    string label = id;
                    try
                    {
                        var sample = ItemRegistry.Create("(O)" + id, 1);
                        if (sample != null) label = sample.DisplayName;
                    }
                    catch { }
                    missing.Add($"{label} x{n - have}");
                }
            }
            if (missing.Count > 0)
                return new { kind = "craft", ok = false, reason = "材料不足：" + string.Join("，", missing), missing };

            int crafted = 0;
            var dropped = new List<string>();
            for (int c = 0; c < Math.Max(1, count); c++)
            {
                foreach (var (id, n) in need)
                {
                    if (Count(inv, id) < n) { return new { kind = "craft", ok = crafted > 0, crafted, reason = "材料不够再做下一个" }; }
                }

                Item item;
                try { item = ItemRegistry.Create("(O)" + outId, 1); }
                catch (Exception e) { return new { kind = "craft", ok = crafted > 0, crafted, reason = "产出生成失败：" + e.Message }; }
                if (item == null) break;
                if (!HasRoomFor(inv, item))
                    return new
                    {
                        kind = "craft",
                        ok = crafted > 0,
                        crafted,
                        reason = "背包满了：先 inventory_action op=place_chest 放箱子或 ship，把东西放进去再制作",
                    };
                foreach (var (id, n) in need) Remove(inv, id, n);
                if (!player.addItemToInventoryBool(item))
                {
                    Game1.currentLocation?.debris.Add(new Debris(item, player.Position));
                    dropped.Add(item.DisplayName);
                }
                crafted++;
            }

            return new
            {
                kind = "craft",
                ok = crafted > 0,
                item = display,
                key,
                crafted,
                dropped,
                reason = crafted > 0 ? (dropped.Count > 0 ? "背包满了，部分掉在地上" : "制作完成") : "没做出来",
            };
        }
    }
}

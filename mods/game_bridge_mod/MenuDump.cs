using System.Collections.Generic;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Objects;

namespace AgentStardew;

// 把当前菜单画面上能点的东西（含商店商品）连坐标一起吐出来，方便外部脚本精确点击。
public static class MenuDump
{
    public static object Dump()
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null) return new { kind = "menudump", type = (string?)null, money = Game1.player != null ? Game1.player.Money : 0, items = new List<object>() };

        var items = new List<object>();
        var comps = menu.allClickableComponents;
        if (comps != null)
        {
            foreach (var c in comps)
            {
                items.Add(new
                {
                    kind = "component",
                    name = c.name,
                    label = c.label,
                    x = c.bounds.X,
                    y = c.bounds.Y,
                    w = c.bounds.Width,
                    h = c.bounds.Height
                });
            }
        }

        if (menu is ShopMenu shop)
        {
            for (int i = 0; i < shop.forSale.Count; i++)
            {
                var it = shop.forSale[i];
                ClickableComponent? btn = i < shop.forSaleButtons.Count ? shop.forSaleButtons[i] : null;
                items.Add(new
                {
                    kind = "sale",
                    index = i,
                    name = it.DisplayName,
                    price = it.salePrice(),
                    x = btn?.bounds.X ?? 0,
                    y = btn?.bounds.Y ?? 0,
                    w = btn?.bounds.Width ?? 0,
                    h = btn?.bounds.Height ?? 0
                });
            }
        }

        // 把菜单里所有 ClickableComponent 类型的字段也吐出来（购买/数量按钮常藏在这里）
        foreach (var fld in menu.GetType().GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
        {
            object? val = null;
            try { val = fld.GetValue(menu); } catch { continue; }
            if (val is ClickableComponent cc)
            {
                items.Add(new { kind = "field", name = fld.Name, label = cc.label, x = cc.bounds.X, y = cc.bounds.Y, w = cc.bounds.Width, h = cc.bounds.Height });
            }
            else if (val is System.Collections.IEnumerable seq && !(val is string))
            {
                int i = 0;
                foreach (var o in seq)
                {
                    if (o is ClickableComponent c2) items.Add(new { kind = "field", name = fld.Name + "[" + i + "]", label = c2.label, x = c2.bounds.X, y = c2.bounds.Y, w = c2.bounds.Width, h = c2.bounds.Height });
                    i++;
                }
            }
        }
        // 商店里玩家背包的格子（买了东西要往这些格子里点）
        foreach (var fld in menu.GetType().GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
        {
            object? val2 = null;
            try { val2 = fld.GetValue(menu); } catch { continue; }
            if (val2 is InventoryMenu invM2 && invM2.inventory != null)
            {
                items.Add(new { kind = "invorigin", name = fld.Name, label = "", x = invM2.xPositionOnScreen, y = invM2.yPositionOnScreen, w = 0, h = 0 });
            }
            if (val2 is InventoryMenu invMenu && invMenu.inventory != null)
            {
                foreach (var slot in invMenu.inventory)
                {
                    if (slot == null) continue;
                    items.Add(new { kind = "invslot", name = slot.name, label = slot.label, x = slot.bounds.X, y = slot.bounds.Y, w = slot.bounds.Width, h = slot.bounds.Height });
                }
            }
        }
        if (menu is ShopMenu sm)
        {
            items.Add(new { kind = "held", name = sm.heldItem?.DisplayName ?? "", x = 0, y = 0, w = 0, h = 0 });
        }
        return new { kind = "menudump", type = menu.GetType().Name, money = Game1.player != null ? Game1.player.Money : 0, items };
    }

    // 直接让游戏自己处理一次点击，绕开系统级的鼠标注入
    public static object Click(int x, int y)
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null) return new { kind = "menuclick", clicked = false, reason = "没有打开的菜单" };
        menu.receiveLeftClick(x, y, true);
        return new { kind = "menuclick", clicked = true, x, y, type = menu.GetType().Name };
    }

    public static object Close()
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null) return new { kind = "menuclose", closed = false };
        var type = menu.GetType().Name;
        menu.exitThisMenu(true);
        return new { kind = "menuclose", closed = true, type };
    }

    // 把点击转发给菜单里的背包（买完东西要往格子里点）
    public static object InvClick(int x, int y)
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null) return new { kind = "menuclickinv", clicked = false, reason = "没有打开的菜单" };
        foreach (var fld in menu.GetType().GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
        {
            object? val = null;
            try { val = fld.GetValue(menu); } catch { continue; }
            if (val is InventoryMenu inv)
            {
                inv.receiveLeftClick(x, y, true);
                return new { kind = "menuclickinv", clicked = true, x, y };
            }
        }
        return new { kind = "menuclickinv", clicked = false, reason = "菜单里没有背包" };
    }

    // 直接在商店里买东西：扣钱、进背包，不经过 UI 点击
    public static object ShopBuy(int index, int qty)
    {
        if (Game1.activeClickableMenu is not ShopMenu shop) return new { kind = "shopbuy", ok = false, reason = "不在商店菜单里" };
        if (index < 0 || index >= shop.forSale.Count) return new { kind = "shopbuy", ok = false, reason = "商品序号越界" };
        if (qty < 1) qty = 1;
        var item = shop.forSale[index];
        int price = item.salePrice();
        int cost = price * qty;
        if (Game1.player.Money < cost)
            return new { kind = "shopbuy", ok = false, reason = "钱不够", need = cost, money = Game1.player.Money };
        int added = 0;
        for (int i = 0; i < qty; i++)
        {
            var copy = (Item)item.GetSalableInstance();
            if (Game1.player.addItemToInventoryBool(copy)) added++;
            else { Game1.createItemDebris(copy, Game1.player.Position, -1); added++; }
        }
        Game1.player.Money -= cost;
        shop.heldItem = null;
        return new { kind = "shopbuy", ok = true, name = item.DisplayName, qty = added, price, cost, money = Game1.player.Money };
    }

    // 造箱子并放到指定格子（消耗 50 木材）
    public static object PlaceChest(int tx, int ty)
    {
        const string woodId = "(O)388";
        int woodCount = 0;
        foreach (var wi in Game1.player.Items) if (wi != null && wi.QualifiedItemId == woodId) woodCount += wi.Stack;
        if (woodCount < 50)
            return new { kind = "placechest", ok = false, reason = "木材不够 50" };
        var loc = Game1.currentLocation;
        var tile = new Microsoft.Xna.Framework.Vector2(tx, ty);
        if (loc.objects.ContainsKey(tile))
            return new { kind = "placechest", ok = false, reason = "那格已经有东西" };
        int need = 50;
        for (int i = 0; i < Game1.player.Items.Count && need > 0; i++)
        {
            var it = Game1.player.Items[i];
            if (it == null || it.QualifiedItemId != woodId) continue;
            int take = System.Math.Min(need, it.Stack);
            it.Stack -= take; need -= take;
            if (it.Stack <= 0) Game1.player.Items[i] = null;
        }
        var item = ItemRegistry.Create("(BC)130");
        if (item is not StardewValley.Object obj)
            return new { kind = "placechest", ok = false, reason = "造不出箱子" };
        bool placed = obj.placementAction(loc, tx * 64, ty * 64, Game1.player);
        if (!placed)
            return new { kind = "placechest", ok = false, reason = "放不下去" };
        return new { kind = "placechest", ok = true, x = tx, y = ty, woodLeft = woodCount - 50 };
    }

    // 把背包某格塞进指定格子上的箱子
    public static object ChestPut(int tx, int ty, int slot)
    {
        var loc = Game1.currentLocation;
        var tile = new Microsoft.Xna.Framework.Vector2(tx, ty);
        if (!loc.objects.TryGetValue(tile, out var obj) || obj is not Chest chest)
            return new { kind = "chestput", ok = false, reason = "那格不是箱子" };
        if (slot < 0 || slot >= Game1.player.Items.Count)
            return new { kind = "chestput", ok = false, reason = "格子越界" };
        var item = Game1.player.Items[slot];
        if (item == null) return new { kind = "chestput", ok = false, reason = "那格是空的" };
        var clone = item.getOne();
        clone.Stack = item.Stack;
        chest.Items.Add(clone);
        Game1.player.Items[slot] = null;
        return new { kind = "chestput", ok = true, name = item.DisplayName, count = item.Stack, chestItems = chest.Items.Count };
    }

    // 把背包某格丢进发货箱（第二天结算）
    public static object Ship(int slot)
    {
        if (slot < 0 || slot >= Game1.player.Items.Count) return new { kind = "ship", ok = false, reason = "格子越界" };
        var item = Game1.player.Items[slot];
        if (item == null) return new { kind = "ship", ok = false, reason = "那格是空的" };
        if (item is Tool) return new { kind = "ship", ok = false, reason = "工具不能卖" };
        var bin = Game1.getFarm().getShippingBin(Game1.player);
        var clone = item.getOne();
        clone.Stack = item.Stack;
        bin.Add(clone);
        Game1.player.Items[slot] = null;
        return new { kind = "ship", ok = true, name = item.DisplayName, count = item.Stack, binCount = bin.Count };
    }

    public static object Bin()
    {
        var bin = Game1.getFarm().getShippingBin(Game1.player);
        var list = new System.Collections.Generic.List<object>();
        foreach (var it in bin) if (it != null) list.Add(new { name = it.DisplayName, count = it.Stack });
        return new { kind = "bin", count = list.Count, items = list };
    }

    // 兜底：把卡住的「模拟输入」强制释放（进程被强杀时锁不会自己松）
    public static object ForceUnlock()
    {
        var fld = typeof(Game1).GetField("inputSimulator", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic);
        if (fld == null) return new { kind = "forceunlock", ok = false, reason = "找不到 inputSimulator 字段" };
        fld.SetValue(null, null);
        return new { kind = "forceunlock", ok = true };
    }

    // 兜底：把角色强行传回指定地图（卡在奇怪地图/事件里时用）
    public static object Warp(string location, int x, int y)
    {
        try
        {
            Game1.warpFarmer(location, x, y, false);
            return new { kind = "warp", ok = true, location, x, y };
        }
        catch (Exception e)
        {
            return new { kind = "warp", ok = false, reason = e.Message };
        }
    }


    // 从指定格子的箱子里把东西拿进背包
    public static object ChestGet(int x, int y, string name = "")
    {
        var loc = Game1.currentLocation;
        var tile = new Microsoft.Xna.Framework.Vector2(x, y);
        if (!loc.objects.TryGetValue(tile, out var obj) || obj is not Chest chest)
            return new { kind = "chestget", ok = false, reason = "这格不是箱子" };
        var player = Game1.player;
        var moved = new List<string>();
        for (var i = chest.Items.Count - 1; i >= 0; i--)
        {
            var item = chest.Items[i];
            if (item == null) continue;
            var nm = item.DisplayName ?? "";
            if (!string.IsNullOrEmpty(name) && !nm.Contains(name) && !(item.QualifiedItemId ?? "").Contains(name)) continue;
            var stack = item.Stack;
            var left = player.addItemToInventory(item);
            if (left == null) { chest.Items.RemoveAt(i); moved.Add(nm + " x" + stack); }
        }
        return new { kind = "chestget", ok = true, moved };
    }
}

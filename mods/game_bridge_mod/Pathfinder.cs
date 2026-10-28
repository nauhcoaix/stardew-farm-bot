using System;
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using StardewValley;

namespace AgentStardew;

// A* 寻路：4 邻接、曼哈顿启发。只走当前玩家能站立的格子，
// 中途不穿过自动切图点（除非目标本身就是切图点）。
internal static class Pathfinder
{
    private static readonly Point[] Directions = { new(0, -1), new(1, 0), new(0, 1), new(-1, 0) };

    //: 搜索上限：超过后按“目标太远/被围住”处理，避免卡住整个游戏线程。
    public const int MaxExpanded = 24000;

    public static List<Point>? Find(GameLocation location, Point start, Point target, bool near, bool allowExit)
    {
        bool Arrived(Point p) => near
            ? Math.Abs(p.X - target.X) + Math.Abs(p.Y - target.Y) <= 1
            : p == target;
        if (!Observations.InBounds(location, target) && !allowExit) return null;

        var open = new PriorityQueue<Point, int>();
        var cost = new Dictionary<Point, int> { [start] = 0 };
        var parent = new Dictionary<Point, Point?> { [start] = null };
        open.Enqueue(start, Heuristic(start, target));

        while (open.TryDequeue(out var current, out _))
        {
            if (Arrived(current)) return Reconstruct(parent, current);
            foreach (var step in Directions)
            {
                var next = current + step;
                if (!Observations.InBounds(location, next)) continue;
                var isExit = allowExit && next == target;
                if (!isExit && !Observations.Walkable(location, next)) continue;
                if (!Arrived(next) && IsWarpTile(location, next)) continue;

                var nextCost = cost[current] + 1;
                if (cost.TryGetValue(next, out var known) && known <= nextCost) continue;
                cost[next] = nextCost;
                parent[next] = current;
                open.Enqueue(next, nextCost + Heuristic(next, target));
                if (cost.Count > MaxExpanded) return null;
            }
        }
        return null;
    }

    public static bool IsWarp(GameLocation location, Point tile)
    {
        foreach (var warp in location.warps)
            if (warp.X == tile.X && warp.Y == tile.Y) return true;
        //: 门大多是 TouchAction/Action 上的 "Warp ..."，只把它们当目标格，不当中途通路。
        foreach (var property in new[] { "TouchAction", "Action" })
        {
            var value = location.doesTileHaveProperty(tile.X, tile.Y, property, "Back");
            if (value?.StartsWith("Warp", StringComparison.Ordinal) == true) return true;
        }
        return false;
    }

    private static int Heuristic(Point from, Point target) =>
        Math.Abs(from.X - target.X) + Math.Abs(from.Y - target.Y);

    private static bool IsWarpTile(GameLocation location, Point tile) => IsWarp(location, tile);

    private static List<Point> Reconstruct(Dictionary<Point, Point?> parent, Point end)
    {
        var nodes = new List<Point>();
        for (var p = end; parent[p] is Point previous; p = previous) nodes.Add(p);
        nodes.Reverse();
        return nodes;
    }
}

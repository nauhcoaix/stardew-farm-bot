using StandaloneFishingSkill;

int count = 0;
void Check(bool condition, string description)
{
    if (!condition) throw new Exception(description);
    count++;
    Console.WriteLine("PASS " + description);
}

Check(FishingPolicy.Hold(100, 0, 400, 0, 100, false), "鱼在上方时按住工具键");
Check(!FishingPolicy.Hold(400, 0, 50, 0, 100, true), "鱼在下方时松开工具键");
Check(!FishingPolicy.Hold(200, 0, 182, -5, 100, true), "接近目标且快速上升时提前制动");
Check(FishingPolicy.CanHook(true, false, false, false, false), "真实咬钩时可以提竿");
Check(!FishingPolicy.CanHook(true, true, false, false, false), "同次咬钩只提竿一次");
Check(!FishingPolicy.CanHook(true, false, true, false, false), "原生自动提竿后不重复触发");

var limits = new Limits { MaxCasts = 5, TargetFish = 2 };
Check(FishingPolicy.EndReason(3, 2, 0, 100, 1200, 100, 5, limits) == "target_fish_reached", "鱼数目标达到后停止");
Check(FishingPolicy.EndReason(5, 1, 0, 100, 1200, 100, 5, limits) == "cast_limit", "达不到鱼数时仍遵守竿数上限");
Check(FishingPolicy.EndReason(2, 0, 3, 100, 1200, 100, 5, limits) == "consecutive_no_catch", "连续失败不会无限抛竿");
Check(FishingPolicy.EndReason(1, 0, 0, 301, 1200, 100, 5, limits) == "time_limit", "遵守墙钟预算");
Check(FishingPolicy.EndReason(1, 0, 0, 10, 2200, 100, 5, limits) == "bedtime", "就寝时间停止");
Check(FishingPolicy.EndReason(1, 0, 0, 10, 1200, 29, 5, limits) == "low_energy", "下一竿前检查体力");
Check(FishingPolicy.EndReason(1, 0, 0, 10, 1200, 100, 0, limits) == "inventory_full", "背包没有空位时不再抛竿");
Check(FishingPolicy.EndReason(1, 0, 0, 10, 1200, 100, 1, limits) == null, "背包剩一格仍可继续钓鱼");
bool bad = false;
try { new Limits { MaxCasts = 1000 }.Validate(); } catch (ArgumentException) { bad = true; }
Check(bad, "游戏端拒绝越界预算");

// 带惯性和边界反弹的简化物理，用于检验控制方向与制动；不代表实机成功率。
float top = 400, velocity = 0, priorFish = 280;
bool held = false;
int inside = 0;
const int height = 128, frames = 2400;
for (var i = 0; i < frames; i++)
{
    float fish = 270 + 170 * (float)Math.Sin(i / 110.0);
    held = FishingPolicy.Hold(fish, fish - priorFish, top, velocity, height, held);
    priorFish = fish;
    velocity += held ? -0.25f : 0.25f;
    top += velocity;
    if (top < 0) { top = 0; velocity = -velocity * 2 / 3; }
    if (top > 568 - height) { top = 568 - height; velocity = -velocity * 2 / 3; }
    if (i > 120 && fish + 12 <= top - 32 + height && fish - 16 >= top - 32) inside++;
}
var coverage = inside / (float)(frames - 121);
Check(coverage > 0.85, $"简化物理下跟随移动目标（覆盖率 {coverage:P1}，仅为模拟）");
Console.WriteLine($"{count} policy checks passed; no live game used.");

namespace StandaloneFishingSkill;

// 只负责输入决策与预算；不依赖游戏 DLL，可离线验证。
public static class FishingPolicy
{
    public static bool CanHook(bool nibbling, bool sent, bool hit, bool pulling, bool reeling)
        => nibbling && !sent && !hit && !pulling && !reeling;

    public static bool Hold(float fish, float fishVelocity, float barTop, float barVelocity, int height, bool prior)
    {
        var target = fish + Math.Clamp(fishVelocity, -3, 3) * 4 + 32 - height / 2f;
        var predicted = barTop + barVelocity * 8;
        var error = predicted - target;
        return error > 1 || (error >= -1 && prior);
    }

    public static string? EndReason(int casts, int fish, int failures, double elapsed, int gameTime,
        float energy, int emptySlots, Limits limits)
    {
        if (limits.TargetFish > 0 && fish >= limits.TargetFish) return "target_fish_reached";
        if (casts >= limits.MaxCasts) return "cast_limit";
        if (failures >= 3) return "consecutive_no_catch";
        if (elapsed >= limits.MaxSeconds) return "time_limit";
        if (gameTime >= limits.StopAt) return "bedtime";
        if (energy < limits.MinEnergy) return "low_energy";
        if (emptySlots < 1) return "inventory_full";
        return null;
    }
}

public sealed class Limits
{
    public int MaxCasts { get; set; } = 5;
    public int TargetFish { get; set; }
    public int MaxSeconds { get; set; } = 300;
    public int StopAt { get; set; } = 2200;
    public int MinEnergy { get; set; } = 30;
    public int Facing { get; set; } = -1;

    public void Validate()
    {
        if (MaxCasts is < 1 or > 100 || TargetFish is < 0 or > 100 || MaxSeconds is < 10 or > 1800
            || MinEnergy is < 15 or > 270 || StopAt is < 600 or > 2300 || StopAt % 100 >= 60
            || Facing is < -1 or > 3)
            throw new ArgumentException("钓鱼参数超出范围");
    }
}

package dev.wizardlauncher.legacypacks;

import net.minecraft.client.Minecraft;
import net.minecraft.world.entity.AreaEffectCloud;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.ambient.AmbientCreature;
import net.minecraft.world.entity.animal.Animal;
import net.minecraft.world.entity.animal.allay.Allay;
import net.minecraft.world.entity.decoration.ArmorStand;
import net.minecraft.world.entity.monster.AbstractSkeleton;
import net.minecraft.world.entity.monster.Endermite;
import net.minecraft.world.entity.monster.PatrollingMonster;
import net.minecraft.world.entity.monster.Silverfish;
import net.minecraft.world.entity.monster.Vex;
import net.minecraft.world.entity.monster.Zombie;
import net.minecraft.world.entity.monster.ZombifiedPiglin;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.projectile.Projectile;

public final class LegacyRiding {
    private LegacyRiding() {
    }

    public static boolean applies(Entity vehicle) {
        if (!vehicle.level().isClientSide) {
            return false;
        }
        boolean plain = vehicle instanceof ArmorStand || vehicle instanceof AmbientCreature || vehicle instanceof Zombie && !(vehicle instanceof ZombifiedPiglin)
            || vehicle instanceof AbstractSkeleton || vehicle instanceof Silverfish || vehicle instanceof Endermite
            || vehicle instanceof Projectile || vehicle instanceof AreaEffectCloud;
        if (!plain) {
            return false;
        }
        return LegacyEffects.fromLegacyWorld(Minecraft.getInstance().getConnection());
    }

    public static double riderY(Entity vehicle, Entity rider) {
        return vehicle.getY() + vehicle.getBbHeight() * 0.75 + ownOffset(rider);
    }

    static double ownOffset(Entity rider) {
        if (rider instanceof ArmorStand stand) {
            return stand.isMarker() ? 0.0 : 0.1;
        }
        if (rider instanceof Player) {
            return -0.35;
        }
        if (rider instanceof AbstractSkeleton) {
            return -0.6;
        }
        if (rider instanceof ZombifiedPiglin piglin) {
            return piglin.isBaby() ? -0.05 : -0.45;
        }
        if (rider instanceof Zombie zombie) {
            return zombie.isBaby() ? 0.0 : -0.45;
        }
        if (rider instanceof PatrollingMonster) {
            return -0.45;
        }
        if (rider instanceof Silverfish || rider instanceof Endermite) {
            return 0.1;
        }
        if (rider instanceof Vex || rider instanceof Allay) {
            return 0.4;
        }
        if (rider instanceof Animal) {
            return 0.14;
        }
        return 0.0;
    }
}

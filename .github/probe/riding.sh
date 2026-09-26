JAR=$(find ~/.gradle/caches -name "minecraft-*merged*$1*.jar" | grep -v sources | head -1)
[ -z "$JAR" ] && JAR=$(find ~/.gradle/caches -name "minecraft-*$1*.jar" | grep -v sources | grep -i "named\|mappings" | head -1)
echo "JAR=$JAR"
mkdir -p /tmp/mc && cd /tmp/mc && unzip -q -o "$JAR" 'net/minecraft/world/entity/*'
for c in Entity decoration/ArmorStand LivingEntity Mob AreaEffectCloud EntityAttachments EntityAttachment EntityType; do
  [ -f net/minecraft/world/entity/$c.class ] || continue
  echo "=== $c"
  javap -c -p net/minecraft/world/entity/$c.class | awk '/(getPassengersRidingOffset|getMyRidingOffset|positionRider|getPassengerRidingPosition|getVehicleAttachmentPoint|getPassengerAttachmentPoint|ridingOffset|getAttachments|createDefault|rideTick|getDefaultPassengerAttachmentPoint)\(/{p=1} p{print} /^$/{p=0}' | head -220
done
echo "=== overrides of riding offsets"
for f in $(grep -rl "getMyRidingOffset\|getPassengersRidingOffset\|getPassengerAttachmentPoint\|getVehicleAttachmentPoint\|ridingOffset" net/minecraft/world/entity --include=*.class); do
  javap -p $f 2>/dev/null | grep -E "RidingOffset|AttachmentPoint" | sed "s|^|$f: |"
done | head -150
echo "=== EntityType ARMOR_STAND builder"
javap -c -p net/minecraft/world/entity/EntityType.class | grep -n -A40 'String armor_stand' | head -60

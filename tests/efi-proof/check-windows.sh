#!/bin/bash
# What Windows owns on an EFI dual-boot machine, read and printed. It writes
# nothing. Run it before and after an upgrade or a restore, then compare:
#
#   sudo bash tests/efi-proof/check-windows.sh > before.txt
#   ... the upgrade or the restore ...
#   sudo bash tests/efi-proof/check-windows.sh > after.txt
#   diff -I '^#' before.txt after.txt
#
# Lines that start with # are facts that change on their own (the time, the
# ESP's free space, shim's SBAT level). diff -I leaves them out. Everything
# else should match, except the section for EFI/ubuntu, which is Ubuntu's own,
# and the one for EFI/BOOT when it holds Ubuntu's shim (the section says).
#
# It reads the disk that holds /boot/efi: its partition table, the boot
# entries, every file on the ESP, and each other partition's raw bytes. A
# partition over 4 GiB is too slow to read whole, so only its first 64 MiB is
# hashed, and the line says so.
set -u

[ "$(id -u)" = 0 ] || { echo "check-windows: run it with sudo" >&2; exit 1; }
esp=$(findmnt -no SOURCE /boot/efi) || { echo "check-windows: /boot/efi is not mounted" >&2; exit 1; }
disk=/dev/$(lsblk -no PKNAME "$esp")

echo "# check-windows $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname): $(. /etc/os-release && echo "$VERSION"), kernel $(uname -r)"
echo "# ESP $esp on $disk: $(df -h --output=size,used,avail /boot/efi | tail -n 1 | tr -s ' ')  (size used avail)"
sbat=/sys/firmware/efi/efivars/SbatLevelRT-605dab50-e046-4300-abb6-3dd810dd8b23
# An EFI variable cannot be seeked, so its 4 bytes of attributes go through cat.
[ -r "$sbat" ] && echo "# SBAT level: $(cat "$sbat" | tail -c +5 | tr -d '\0' | paste -sd' ' -)"
echo "# $(mokutil --sb-state 2>/dev/null | paste -sd' ' - | grep . || echo 'Secure Boot state unknown (no mokutil)')"

echo "== boot entries (efibootmgr -v)"
efibootmgr -v

echo "== partition table of $disk (sfdisk -d)"
sfdisk -d "$disk"

echo "== filesystems on $disk (blkid)"
for p in $(lsblk -nlo PATH "$disk" | tail -n +2); do
    echo "$p $(blkid -o export "$p" | grep -E '^(TYPE|LABEL|UUID|PARTLABEL|PARTUUID)=' | paste -sd' ' -)"
done

echo "== ESP files outside EFI/ubuntu and EFI/BOOT (sha256)"
(cd /boot/efi && find . \( -ipath ./efi/ubuntu -o -ipath ./efi/boot \) -prune -o -type f -print0 \
    | sort -z | xargs -0 -r sha256sum)

# EFI/BOOT is the firmware's fallback, whoever put it there. Ubuntu's shim
# puts fbx64.efi beside its copy, and every shim update rewrites the folder
# (seen on the VM, 2026-10-16). Windows' copy comes without one.
echo "== ESP files in EFI/BOOT, the fallback (sha256)"
if find /boot/efi -ipath '/boot/efi/efi/boot/fbx64.efi' | grep -q .; then
    echo "# it holds Ubuntu's shim (fbx64.efi is there), so it may change"
else
    echo "# no fbx64.efi: not Ubuntu's shim, so it should not change"
fi
(cd /boot/efi && find . -ipath './efi/boot/*' -type f -print0 | sort -z | xargs -0 -r sha256sum)

echo "== ESP files in EFI/ubuntu (sha256; Ubuntu's own, so these may change)"
(cd /boot/efi && find . -ipath './efi/ubuntu/*' -type f -print0 | sort -z | xargs -0 -r sha256sum)

echo "== other partitions on $disk, raw bytes (sha256)"
for p in $(lsblk -nlo PATH "$disk" | tail -n +2); do
    [ "$p" = "$esp" ] && continue
    size=$(blockdev --getsize64 "$p")
    if [ "$size" -le $((4 << 30)) ]; then
        echo "$(sha256sum < "$p" | cut -d' ' -f1)  $p (all $size bytes)"
    else
        echo "$(head -c 64M "$p" | sha256sum | cut -d' ' -f1)  $p (first 64 MiB of $size bytes)"
    fi
done

echo "== GRUB menu, top level"
# A title is in single quotes, or double (23.04's memtest entry).
grep -E "^(menuentry|submenu) " /boot/grub/grub.cfg | sed -E "s/^[a-z]+ ['\"]([^'\"]*)['\"].*/\1/"

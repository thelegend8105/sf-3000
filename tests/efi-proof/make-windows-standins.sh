#!/bin/bash
# Stand-in Windows parts for the EFI proof VM (README.md here). Test tooling
# for a VM: never run it on a machine that has a real Windows.
#
#   sudo bash tests/efi-proof/make-windows-standins.sh
#
# It gives the VM what lab computer 03 has besides Ubuntu (its report of
# 2026-10-14), with random bytes in place of Windows' files:
#   - Microsoft reserved, Windows (NTFS) and recovery (NTFS) partitions in the
#     rest of the NVMe disk, after the ESP;
#   - EFI/Microsoft (27 MB) and EFI/HP (9.2 MB) on the ESP, and a filler file
#     so that the ESP has 26 MB free;
#   - a "Windows Boot Manager" boot entry, second after ubuntu;
#   - GRUB_TIMEOUT=10, as Ubuntu's installer sets it when it finds Windows,
#     then update-grub, so that os-prober puts Windows in GRUB's menu.
# It refuses if the NVMe disk has anything but the ESP on it.
set -euo pipefail

die() { echo "make-windows-standins: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run it with sudo"
disk=/dev/nvme0n1
esp=$(findmnt -no SOURCE /boot/efi) || die "/boot/efi is not mounted"
[ "$esp" = "${disk}p1" ] || die "/boot/efi is $esp, not ${disk}p1"
[ "$(lsblk -nlo NAME "$disk" | wc -l)" = 2 ] || die "$disk has more than the ESP on it"
[ ! -e /boot/efi/EFI/Microsoft ] || die "the ESP has EFI/Microsoft already"
command -v mkntfs >/dev/null || die "mkntfs is missing: sudo apt-get install ntfs-3g"

# The partitions, with the ESP unmounted so the kernel can re-read the table.
umount /boot/efi
sfdisk --quiet --append "$disk" <<'EOF'
size=16MiB, type=E3C9E316-0B5C-4DB8-817D-F92DF00215AE, name="Microsoft reserved partition"
size=1200MiB, type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7, name="Basic data partition"
type=DE94BBA4-06D1-4D40-A16A-BFD50179D6AC
EOF
udevadm settle
mount /boot/efi
mkntfs --quick --quiet --label Windows "${disk}p3"
mkntfs --quick --quiet --label Recovery "${disk}p4"
# mkntfs seeds its serial number from the clock, so two made in one second
# share it. Windows' two volumes would not.
ntfslabel --new-serial="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')" "${disk}p4" Recovery

# Something on the Windows partition, so its bytes are not all zeros.
mnt=$(mktemp -d)
mount -t ntfs-3g "${disk}p3" "$mnt"
mkdir -p "$mnt/Windows/System32" "$mnt/Users/student/Documents"
head -c 8M /dev/urandom > "$mnt/Windows/System32/ntoskrnl.exe"
echo "stand-in for a student's file" > "$mnt/Users/student/Documents/marks.txt"
umount "$mnt"
rmdir "$mnt"

# The ESP. The filler stands in for the 30 MB more that 03's EFI/ubuntu holds.
e=/boot/efi/EFI
mkdir -p "$e/Microsoft/Boot/en-US" "$e/Microsoft/Boot/Fonts" "$e/Microsoft/Recovery" "$e/HP/BIOS/Current"
head -c 2600K /dev/urandom > "$e/Microsoft/Boot/bootmgfw.efi"
head -c 2600K /dev/urandom > "$e/Microsoft/Boot/bootmgr.efi"
head -c 1600K /dev/urandom > "$e/Microsoft/Boot/memtest.efi"
head -c 32K /dev/urandom > "$e/Microsoft/Boot/BCD"
head -c 80K /dev/urandom > "$e/Microsoft/Boot/en-US/bootmgfw.efi.mui"
head -c 20M /dev/urandom > "$e/Microsoft/Boot/Fonts/fonts.bin"
head -c 32K /dev/urandom > "$e/Microsoft/Recovery/BCD"
head -c 9400K /dev/urandom > "$e/HP/BIOS/Current/firmware.bin"
avail_kb=$(df -k --output=avail /boot/efi | tail -n 1)
fill_kb=$((avail_kb - 26 * 1024))
if [ "$fill_kb" -gt 0 ]; then
    head -c "${fill_kb}K" /dev/urandom > /boot/efi/sf3000-space-filler.bin
fi

# The boot entry. efibootmgr puts a new entry first; 03 has Windows second.
entry() { efibootmgr | { grep -E "^Boot[0-9A-F]{4}\*? $1(\s|$)" || true; } | cut -c5-8; }
ubuntu=$(entry ubuntu)
[ "$(echo "$ubuntu" | wc -w)" = 1 ] || die "expected one ubuntu boot entry, found: $ubuntu"
efibootmgr --quiet --create --disk "$disk" --part 1 \
    --label "Windows Boot Manager" --loader '\EFI\Microsoft\Boot\bootmgfw.efi'
windows=$(entry "Windows Boot Manager")
rest=$(efibootmgr | sed -n 's/^BootOrder: //p' | tr , '\n' \
       | { grep -vx -e "$ubuntu" -e "$windows" || true; } | paste -sd, -)
efibootmgr --quiet --bootorder "$ubuntu,$windows${rest:+,$rest}"

# GRUB, as Ubuntu's installer leaves it beside a Windows. Ubuntu runs
# os-prober at GRUB's first update-grub, and after that only while grub.cfg
# holds an entry it found (GRUB_DISABLE_OS_PROBER=auto, /etc/grub.d/30_os-prober).
# Here grub.cfg exists without one, so it is moved aside for one first run.
sed -i 's/^GRUB_TIMEOUT=0$/GRUB_TIMEOUT=10/' /etc/default/grub
mv /boot/grub/grub.cfg /boot/grub/grub.cfg.before-standins
if ! update-grub; then
    mv /boot/grub/grub.cfg.before-standins /boot/grub/grub.cfg
    die "update-grub failed; the old grub.cfg is back"
fi

echo
efibootmgr
df -h /boot/efi
lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTLABEL "$disk"
grep -q "Windows Boot Manager" /boot/grub/grub.cfg \
    && echo "GRUB's menu lists Windows Boot Manager" \
    || echo "WARNING: GRUB's menu does not list Windows Boot Manager"

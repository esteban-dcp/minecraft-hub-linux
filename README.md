<div align="center">

# 🟩 Minecraft Hub for Linux

**A launcher for the Minecraft family of games on Linux — Bedrock today,
Dungeons and Legends next — with real Xbox sign-in, friends, servers and Realms.**

<!-- placeholder: download badge points at the upstream BedrockOnLinux
   release until this fork has its own. Replace once minecraft-hub-linux has
   a tagged release.
   [![Download](https://img.shields.io/github/v/release/Wyze3306/BedrockOnLinux?style=for-the-badge&logo=github&logoColor=white&label=Download&color=2ea043)](https://github.com/Wyze3306/BedrockOnLinux/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Wyze3306/BedrockOnLinux/total?style=for-the-badge&logo=github&logoColor=white&label=Downloads&color=444d56)](https://github.com/Wyze3306/BedrockOnLinux/releases)
<!-- placeholder: website points at upstream until this fork has pages.
   [![Website](https://img.shields.io/badge/Website-0b7285?style=for-the-badge&logo=googlechrome&logoColor=white)](https://wyze3306.github.io/BedrockOnLinux/)
[![Discord](https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/5YJq54Yhbu)
[![License](https://img.shields.io/badge/License-MIT-6e7781?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)

![Ubuntu](https://img.shields.io/badge/Ubuntu-E95420?style=flat-square&logo=ubuntu&logoColor=white)
![Debian](https://img.shields.io/badge/Debian-A81D33?style=flat-square&logo=debian&logoColor=white)
![Linux Mint](https://img.shields.io/badge/Mint%20%2F%20LMDE-87CF3E?style=flat-square&logo=linuxmint&logoColor=white)
![Fedora](https://img.shields.io/badge/Fedora-51A2DA?style=flat-square&logo=fedora&logoColor=white)
![Arch](https://img.shields.io/badge/Arch-1793D1?style=flat-square&logo=archlinux&logoColor=white)
![openSUSE](https://img.shields.io/badge/openSUSE-73BA25?style=flat-square&logo=opensuse&logoColor=white)
![Steam Deck](https://img.shields.io/badge/Steam%20Deck-1A9FFF?style=flat-square&logo=steamdeck&logoColor=white)
![NixOS](https://img.shields.io/badge/NixOS-5277C3?style=flat-square&logo=nixos&logoColor=white)

![Minecraft Hub for Linux launcher](screenshot.png)

</div>

## What it is

Minecraft Hub for Linux installs and runs the Windows (GDK) editions of
Minecraft through Microsoft's own downloader with your own account. Bedrock
ships in this release; Dungeons and Legends plug into the same registry,
prebuilt engine and launcher as they become available. It downloads the game from the Microsoft Store with your own account,
sets everything up for you, and starts it. No Windows, no second machine,
nothing to compile.

You sign in to Microsoft from inside Minecraft, exactly as on Windows, so
Friends, invitations, public servers, Realms and the Marketplace work like they
should. Nothing goes through a third party.

You can also play without an account: single-player worlds and LAN games work
offline, only the online features are out of reach. Achievements show up in the
game, but they don't unlock yet.

## Install

Download the file you want from the
[latest release](https://github.com/Wyze3306/BedrockOnLinux/releases/latest).

| Format | Best for | How to start it |
|---|---|---|
| AppImage | Most Linux desktops | `./MinecraftHub-*-x86_64.AppImage` |
| `.deb` | Debian, Ubuntu, Mint, LMDE | `sudo apt install ./minecraft-hub_*_amd64.deb` |
| `.rpm` | Fedora, Nobara | `sudo dnf install ./minecraft-hub-*.x86_64.rpm` |
| Flatpak | Atomic systems such as Bazzite | `flatpak install --user ./MinecraftHub-*-x86_64.flatpak` |
| Nix | NixOS, or any Linux with Nix installed | `nix run github:esteban-dcp/minecraft-hub-linux` |

### Nix / NixOS

Try it without installing anything:

```bash
nix run github:esteban-dcp/minecraft-hub-linux
```

Or add it as a flake input to install it declaratively, for example into
`environment.systemPackages`:

```nix
# flake.nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    minecraft-hub = {
      url = "github:esteban-dcp/minecraft-hub-linux";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, minecraft-hub, ... }: {
    nixosConfigurations.your-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        {
          environment.systemPackages = [
            minecraft-hub.packages.x86_64-linux.default
          ];
        }
        # ...your other modules
      ];
    };
  };
}
```

## Play

1. Open **Minecraft Hub for Linux** and sign in with the Microsoft account
   that owns the games. It is asked for twice, once to download the game from the
   Microsoft Store, once to play online, because the Store needs a session
   of its own. Use the same account both times; the launcher offers the
   second sign-in right after the first, and again if a download needs it.
2. Pick the game (Bedrock today, Dungeons and Legends next), then the
   edition and version, and hit **PLAY**.
3. Play, including the **Friends**, **Servers** and **Realms** tabs.

The first launch downloads the game and everything it needs, so give it a
while; after that it starts straight away. You can install an older version
too, which is handy when a server hasn't updated yet. The launcher
is multi-game by design: `minecraft-hub setup --game dungeons` installs
Minecraft Dungeons; the same install path works for every other entry in
the registry.

The launcher can be used with a controller — the d-pad or left stick moves the
highlight, **A** selects, **B** goes back, the shoulder buttons change tab and
**Start** plays — and **Tools ▸ Create direct launch shortcut** adds a
*Minecraft Bedrock* entry to your app menu or to Steam. That is the one to use
on a Steam Deck. (The legacy `bedrock-on-linux` binary is still installed
alongside `minecraft-hub` so shell history and Steam shortcuts from
BedrockOnLinux 2.x keep working.)

## What you need

- A 64-bit Linux desktop, reasonably up to date.
- A graphics card and driver that support Vulkan: anything from the last few
  years, with the driver your distribution ships.
- A Microsoft account that owns Minecraft, since the game is downloaded under
  your own licence.
- Enough free disk space for the game and its runtime, a few gigabytes.

## If something goes wrong

Start with the built-in check, which looks at your system and tells you what is
wrong:

```bash
minecraft-hub doctor     # the legacy `bedrock-on-linux` works too
```

If the game itself misbehaves after an update or a crash, `minecraft-hub
repair` rebuilds the Windows environment without touching your worlds. Logs are
one click away in **Settings**, and the same commands are available from the
AppImage or Flatpak through their own entry point.

Still stuck? Open an
[issue](https://github.com/esteban-dcp/minecraft-hub-linux/issues) with your
launcher version, your distribution, your GPU and the log, but never your
account details.

## Building

Everything is built from source by a public, reproducible pipeline, and each
release is signed. The details are in [`docs/BUILD.md`](docs/BUILD.md).

## Legal

Minecraft Hub for Linux ships **no game files**. Each game is downloaded
from Microsoft's own servers, under your own account's licence, by
[Xodus](https://github.com/xodus-gaming/xodus), so you have to own the games
you install, and the terms that come with each one still apply.

Minecraft Hub for Linux is a fork of
[BedrockOnLinux](https://github.com/Wyze3306/BedrockOnLinux) and is MIT
licensed — see [`LICENSE`](LICENSE); the components it bundles keep their own
licences. This is an independent project, not affiliated with or supported by
Mojang or Microsoft.

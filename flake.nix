{
  description = "Install and play Minecraft Bedrock, Dungeons and Legends on Linux";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      pkgs = import nixpkgs {
        system = "x86_64-linux";
        config.allowUnfree = true;
      };
      bolPython = pkgs.python312.withPackages (ps: with ps; [
        pyside6
        cryptography
        packaging
        python-xlib
        certifi
      ]);
      steam-run = pkgs.steam-run.override (prev: {
        targetPkgs = pkgs: prev.targetPkgs pkgs ++ [ pkgs.libxcomposite ];
      });
    in
    {
      packages.x86_64-linux.default = pkgs.stdenv.mkDerivation {
        pname = "minecraft-hub";
        version = "2.2.7";

        src = ./.;

        nativeBuildInputs = [ pkgs.makeWrapper ];

        installPhase = ''
          mkdir -p $out/lib/minecraft-hub $out/bin $out/share/applications $out/share/icons/hicolor/256x256/apps

          cp -r bol $out/lib/minecraft-hub/
          cp minecraft-hub $out/lib/minecraft-hub/
          # Compatibility shim so users with `bedrock-on-linux` in their shell
          # history or Steam shortcuts keep working.
          cp bedrock-on-linux $out/lib/minecraft-hub/

          cp data/minecraft-hub.desktop $out/share/applications/
          cp data/icon.png $out/share/icons/hicolor/256x256/apps/minecraft-hub.png

          # Qt shows GTK's file chooser on GTK desktops, and GTK aborts when
          # its org.gtk.Settings.FileChooser schema is nowhere on
          # XDG_DATA_DIRS -- which NixOS does not put there for us (#263).
          makeWrapper ${steam-run}/bin/steam-run $out/bin/minecraft-hub \
            --add-flags "${bolPython}/bin/python3" \
            --add-flags "$out/lib/minecraft-hub/minecraft-hub" \
            --prefix PYTHONPATH : "$out/lib/minecraft-hub" \
            --suffix XDG_DATA_DIRS : "${pkgs.gtk3}/share/gsettings-schemas/${pkgs.gtk3.name}"

          # Same wrapper, second entry: keep the old binary name as a shim.
          makeWrapper ${steam-run}/bin/steam-run $out/bin/bedrock-on-linux \
            --add-flags "${bolPython}/bin/python3" \
            --add-flags "$out/lib/minecraft-hub/bedrock-on-linux" \
            --prefix PYTHONPATH : "$out/lib/minecraft-hub" \
            --suffix XDG_DATA_DIRS : "${pkgs.gtk3}/share/gsettings-schemas/${pkgs.gtk3.name}"
        '';

        meta = {
          homepage = "https://github.com/esteban-dcp/minecraft-hub-linux";
          license = pkgs.lib.licenses.mit;
          mainProgram = "minecraft-hub";
        };
      };
    };
}

{
  description = "WSJT-X remote web controller";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system:
          f (import nixpkgs { inherit system; }));
    in
    {
      packages = forAllSystems (pkgs:
        let
          lib = pkgs.lib;
          python = pkgs.python3.withPackages (ps: [ ps.aiohttp ]);

          frontend = pkgs.stdenvNoCC.mkDerivation {
            pname = "wsjt-remote-frontend";
            version = "0.1.0";
            src = lib.cleanSourceWith {
              src = ./frontend;
              filter = path: type:
                let
                  rel = lib.removePrefix (toString ./frontend + "/") (toString path);
                in
                !(lib.hasPrefix "dist/" rel);
            };

            nativeBuildInputs = [ pkgs.esbuild pkgs.typescript ];

            buildPhase = ''
              runHook preBuild
              tsc --noEmit
              esbuild src/app.ts src/debug.ts \
                --bundle \
                --format=esm \
                --target=es2020 \
                --external:lit \
                --external:lit/* \
                --outdir=dist
              sha256sum dist/app.js dist/debug.js theme.css | sha256sum | cut -c1-16 > .cache-buster
              runHook postBuild
            '';

            installPhase = ''
              runHook preInstall
              mkdir -p $out
              cache_buster="$(cat .cache-buster)"
              substitute index.html $out/index.html --subst-var-by cacheBuster "$cache_buster"
              substitute debug.html $out/debug.html --subst-var-by cacheBuster "$cache_buster"
              cp theme.css $out/theme.css
              cp -r dist $out/
              runHook postInstall
            '';
          };

          backend = pkgs.stdenvNoCC.mkDerivation {
            pname = "wsjt-remote-backend";
            version = "0.1.0";
            src = lib.cleanSourceWith {
              src = ./backend;
              filter = path: type:
                let
                  rel = lib.removePrefix (toString ./backend + "/") (toString path);
                in
                !(lib.hasInfix "__pycache__" rel) && !(lib.hasSuffix ".pyc" rel);
            };

            nativeBuildInputs = [ python ];

            buildPhase = ''
              runHook preBuild
              python -m compileall wsjtx_remote
              runHook postBuild
            '';

            installPhase = ''
              runHook preInstall

              mkdir -p $out/share/wsjt-remote-backend
              cp -r . $out/share/wsjt-remote-backend/

              mkdir -p $out/bin
              cat > $out/bin/wsjt-remote-backend <<EOF
              #!${pkgs.runtimeShell}
              set -euo pipefail
              export PATH=${pkgs.niri}/bin:${pkgs.wtype}/bin:\$PATH
              exec ${python}/bin/python $out/share/wsjt-remote-backend/start.py "\$@"
              EOF
              chmod +x $out/bin/wsjt-remote-backend

              runHook postInstall
            '';

            meta = {
              mainProgram = "wsjt-remote-backend";
              description = "WSJT-X remote backend server";
              license = lib.licenses.mit;
              platforms = lib.platforms.linux;
            };
          };

          wsjt-remote = pkgs.stdenvNoCC.mkDerivation {
            pname = "wsjt-remote";
            version = "0.1.0";
            dontUnpack = true;
            buildInputs = [ backend frontend ];

            installPhase = ''
              runHook preInstall

              mkdir -p $out/bin
              cat > $out/bin/wsjt-remote <<EOF
              #!${pkgs.runtimeShell}
              set -euo pipefail

              export PATH=${pkgs.niri}/bin:${pkgs.wtype}/bin:\$PATH

              exec ${backend}/bin/wsjt-remote-backend --static-dir ${frontend} "\$@"
              EOF
              chmod +x $out/bin/wsjt-remote

              runHook postInstall
            '';

            meta = {
              mainProgram = "wsjt-remote";
              description = "Browser remote control for WSJT-X over UDP";
              license = lib.licenses.mit;
              platforms = lib.platforms.linux;
            };
          };
        in
        {
          inherit backend wsjt-remote;
          default = wsjt-remote;
        });
    };
}

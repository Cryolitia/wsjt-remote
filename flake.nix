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
              sha256sum dist/app.js dist/debug.js | sha256sum | cut -c1-16 > .cache-buster
              runHook postBuild
            '';

            installPhase = ''
              runHook preInstall
              mkdir -p $out
              cache_buster="$(cat .cache-buster)"
              substitute index.html $out/index.html --subst-var-by cacheBuster "$cache_buster"
              substitute debug.html $out/debug.html --subst-var-by cacheBuster "$cache_buster"
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

            installPhase = ''
              runHook preInstall

              mkdir -p $out/bin
              cat > $out/bin/wsjt-remote <<EOF
              #!${pkgs.runtimeShell}
              set -euo pipefail

              export PATH=${pkgs.niri}/bin:${pkgs.wtype}/bin:\$PATH

              frontend_host="\''${FRONTEND_HOST:-127.0.0.1}"
              frontend_port="\''${FRONTEND_PORT:-5173}"

              ${backend}/bin/wsjt-remote-backend --static-dir ${frontend} "\$@" &
              backend_pid="\$!"

              echo "Frontend: http://\$frontend_host:\$frontend_port/"
              cd ${frontend}
              ${python}/bin/python -c '
              import http.server
              import sys

              host = sys.argv[1]
              port = int(sys.argv[2])

              class Handler(http.server.SimpleHTTPRequestHandler):
                  def end_headers(self):
                      self.send_header("Cache-Control", "no-store, max-age=0")
                      super().end_headers()

              http.server.ThreadingHTTPServer((host, port), Handler).serve_forever()
              ' "\$frontend_host" "\$frontend_port" &
              frontend_pid="\$!"

              cleanup() {
                kill "\$backend_pid" 2>/dev/null || true
                kill "\$frontend_pid" 2>/dev/null || true
                wait "\$backend_pid" 2>/dev/null || true
                wait "\$frontend_pid" 2>/dev/null || true
              }
              trap cleanup EXIT INT TERM

              wait -n "\$backend_pid" "\$frontend_pid"
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

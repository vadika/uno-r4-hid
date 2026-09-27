{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
    in {
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [
          pkgs.arduino-cli
          (pkgs.python3.withPackages (p: [ p.pyserial p.evdev p.pyusb ]))
        ];
        shellHook = ''
          export ARDUINO_DIRECTORIES_DATA="$PWD/.arduino/data"
          export ARDUINO_DIRECTORIES_DOWNLOADS="$PWD/.arduino/downloads"
          export ARDUINO_DIRECTORIES_USER="$PWD/.arduino/user"
        '';
      };
    };
}

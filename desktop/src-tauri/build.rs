// Tauri's build script: it reads `tauri.conf.json`, embeds the assets from `frontendDist` and
// generates the Windows resource file. Nothing here is ours to decide.
fn main() {
    tauri_build::build()
}

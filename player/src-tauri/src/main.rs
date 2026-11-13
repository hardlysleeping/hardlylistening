// The desktop entry point. A mobile build reaches `run` through the generated project's
// `mobile_entry_point` instead, so this file is compiled for desktop targets only.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    hardlylistening_player::run()
}

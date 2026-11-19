//! Console-free Windows launches for background helpers. Pipes and exit handling stay owned by callers.
pub fn background(command: &mut std::process::Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    #[cfg(not(windows))]
    let _ = command;
}

#[cfg(all(test, windows))]
mod tests {
    #[test]
    fn background_child_has_no_console_and_keeps_output_and_exit_status() {
        let mut command = std::process::Command::new("powershell.exe");
        command.args(["-NoProfile", "-NonInteractive", "-Command",
            r#"Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class ConsoleProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'; if ([ConsoleProbe]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 91 }; [Console]::Out.Write('pipe-ok'); exit 23"#]);
        super::background(&mut command);
        let output = command.output().expect("start local console probe");
        assert_eq!(output.status.code(), Some(23), "{}", String::from_utf8_lossy(&output.stderr));
        assert_eq!(String::from_utf8_lossy(&output.stdout), "pipe-ok");
    }
}

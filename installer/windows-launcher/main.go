// Windows launcher: extracts the filled installer script and runs it.
// Built as Clinical-Note-Labeller-Setup.exe so a client can double-click
// instead of opening a .bat.
package main

import (
	_ "embed"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

//go:embed setup.bat
var setupBat []byte

func main() {
	dir := filepath.Join(os.Getenv("LOCALAPPDATA"), "ClinicalNoteLabeller")
	if err := os.MkdirAll(dir, 0o755); end(err) {
		return
	}
	bat := filepath.Join(dir, "setup.bat")
	if err := os.WriteFile(bat, setupBat, 0o644); end(err) {
		return
	}

	self, _ := os.Executable()
	cmd := exec.Command("cmd", "/c", bat)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = append(os.Environ(), "CNL_LAUNCHER="+self)
	if err := cmd.Run(); err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			os.Exit(exit.ExitCode())
		}
		end(err)
	}
}

func end(err error) bool {
	if err == nil {
		return false
	}
	fmt.Fprintf(os.Stderr, "\n[X] %v\n\nPress Enter to close.\n", err)
	fmt.Scanln()
	os.Exit(1)
	return true
}

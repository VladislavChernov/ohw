// Builtin-guard prompt loading for the dz5 stand agent.
//
// Both prompt files are loaded at startup so BUILTIN_GUARDS can be switched at
// runtime without restarting the process. The env value only selects the
// initial prompt (minimal for off, guarded for on).
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func loadPrompts() (minimal, guarded string, err error) {
	dir := os.Getenv("PROMPTS_DIR")
	if dir == "" {
		dir = "prompts"
	}
	minimal, err = readPrompt(filepath.Join(dir, "minimal.txt"))
	if err != nil {
		return "", "", err
	}
	guarded, err = readPrompt(filepath.Join(dir, "guarded.txt"))
	if err != nil {
		return "", "", err
	}
	return minimal, guarded, nil
}

func readPrompt(path string) (string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read prompt %s: %w", filepath.Base(path), err)
	}
	p := strings.TrimSpace(string(raw))
	if p == "" {
		return "", fmt.Errorf("prompt %s is empty", filepath.Base(path))
	}
	return p, nil
}

// Builtin-guard switch for the dz5 stand agent.
//
// Milestone add-agent-core: this file is the seam for the BUILTIN_GUARDS flag.
// Preset 1 (Baseline) starts with prompts/minimal.txt; presets 2/4 will use
// prompts/guarded.txt once milestone add-builtin-guards fills it with guard
// instructions. The prompt loading itself (file selection by env) lives here
// from the start so later milestones only change prompt content.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// loadPrompt selects the system prompt file by BUILTIN_GUARDS and returns its
// content: prompts/minimal.txt for off (presets 1/3), prompts/guarded.txt
// for on (presets 2/4). PROMPTS_DIR overrides the directory (tests, custom
// layouts); otherwise the ./prompts directory next to the working dir is used.
func loadPrompt() (string, error) {
	dir := os.Getenv("PROMPTS_DIR")
	if dir == "" {
		dir = "prompts"
	}
	name := "minimal.txt"
	if strings.EqualFold(os.Getenv("BUILTIN_GUARDS"), "on") {
		name = "guarded.txt"
	}
	raw, err := os.ReadFile(filepath.Join(dir, name))
	if err != nil {
		return "", fmt.Errorf("read prompt %s: %w", name, err)
	}
	p := strings.TrimSpace(string(raw))
	if p == "" {
		return "", fmt.Errorf("prompt %s is empty", name)
	}
	return p, nil
}

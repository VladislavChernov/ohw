package agentcore

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func LoadPrompts(dir string) (minimal, guarded string, err error) {
	if strings.TrimSpace(dir) == "" {
		dir = "prompts"
	}
	minimal, err = ReadPrompt(filepath.Join(dir, "minimal.txt"))
	if err != nil {
		return "", "", err
	}
	guarded, err = ReadPrompt(filepath.Join(dir, "guarded.txt"))
	if err != nil {
		return "", "", err
	}
	return minimal, guarded, nil
}

func ReadPrompt(path string) (string, error) {
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

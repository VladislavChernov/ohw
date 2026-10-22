package main

import (
	"fmt"
	"regexp"
	"strings"
)

type FixedGuard struct {
	input  []*regexp.Regexp
	output []*regexp.Regexp
}

func NewFixedGuard() *FixedGuard {
	inputPatterns := []string{
		`(?i)(ignore|forget|disregard|override).{0,48}(instruction|prompt|policy|rule|system)`,
		`(?i)(reveal|print|show|repeat|dump|leak).{0,48}(system prompt|initial message|hidden context|internal instruction|security policy)`,
		`(?i)(act as|you are now|jailbreak|dan mode|developer mode|fake role)`,
		`(?i)(base64|rot13|rot-13|zero[- ]width|unicode escape)`,
		`(?i)https?://\S+`,
	}
	outputPatterns := []string{
		`(?i)(system prompt|initial message|hidden context|internal instructions|security policy)`,
		`(?i)(password|api[_-]?key|secret|token|passport|inn|snils|card|pin)[_-]?\s*[:=]\s*\S+`,
		`(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}`,
		`(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-]?)\d{3}[ .-]\d{4}`,
		`\b\d{4}\s?\d{6}\b`,
		`\b(?:\d[ -]*?){13,19}\b`,
	}
	g := &FixedGuard{}
	for _, p := range inputPatterns {
		g.input = append(g.input, regexp.MustCompile(p))
	}
	for _, p := range outputPatterns {
		g.output = append(g.output, regexp.MustCompile(p))
	}
	return g
}

func (g *FixedGuard) CheckInput(msg string) error {
	for _, re := range g.input {
		if re.MatchString(strings.TrimSpace(msg)) {
			return fmt.Errorf("fixed input policy violation")
		}
	}
	return nil
}

func (g *FixedGuard) CheckOutput(reply string) error {
	for _, re := range g.output {
		if re.MatchString(reply) {
			return fmt.Errorf("fixed output policy violation")
		}
	}
	return nil
}

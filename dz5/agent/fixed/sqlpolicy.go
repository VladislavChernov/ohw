package main

import (
	"fmt"
	"regexp"
	"strings"
)

var (
	fixedTables    = map[string]bool{"users": true, "personal_data": true, "clients": true, "orders": true, "documents": true}
	tableRef       = regexp.MustCompile(`(?i)\b(?:from|join)\s+([a-z_][a-z0-9_]*)`)
	selectStar     = regexp.MustCompile(`(?i)^\s*select\s+\*\s+from\b`)
	forbiddenWords = []string{
		"insert", "update", "delete", "drop", "alter", "attach", "detach",
		"pragma", "vacuum", "create", "replace", "truncate", "union", "with",
		"sqlite_master", "sqlite_schema", "secrets",
	}
)

func ValidateFixedSQL(rawSQL string, allowWrites bool) error {
	if allowWrites {
		return fmt.Errorf("fixed agent never enables write tools")
	}
	q := strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(rawSQL), ";"))
	if q == "" {
		return fmt.Errorf("empty SQL")
	}
	if strings.Contains(q, ";") {
		return fmt.Errorf("stacked statements are not allowed")
	}
	low := strings.ToLower(q)
	for _, word := range forbiddenWords {
		if strings.Contains(low, word) {
			return fmt.Errorf("forbidden SQL construct: %s", word)
		}
	}
	if !strings.HasPrefix(strings.ToUpper(q), "SELECT ") {
		return fmt.Errorf("only a single SELECT is allowed")
	}
	if selectStar.MatchString(q) {
		return fmt.Errorf("SELECT * is not allowed; request explicit columns or an aggregate")
	}
	refs := tableRef.FindAllStringSubmatch(q, -1)
	if len(refs) == 0 {
		return fmt.Errorf("SELECT must reference a known table")
	}
	for _, ref := range refs {
		if !fixedTables[strings.ToLower(ref[1])] {
			return fmt.Errorf("table is not allowed: %s", ref[1])
		}
	}
	return nil
}

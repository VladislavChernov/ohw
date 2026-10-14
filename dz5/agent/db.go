// db_query tool for the dz5 stand agent.
//
// Milestone add-agent-core, preset 1 (Baseline): the tool executes the SQL the
// LLM emits against the stand SQLite DB and runs the tool-loop
// (LLM -> db_query -> LLM -> reply). Read-only is enforced by default via an
// allow-list on the first keyword; ALLOW_WRITES=1 opens modifications for the
// LLM03 tool-abuse demonstration (state is recoverable via db-restore).
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// sqlFence matches a ```sql ... ``` fenced block the LLM may emit.
var sqlFence = regexp.MustCompile("(?s)```\\s*sql\\s*(.*?)```")

// maxToolIterations bounds the LLM -> db_query -> LLM loop (L3 frugality:
// a runaway model must not spin the loop forever).
const maxToolIterations = 3

// isReadQuery reports whether q is a single read-only SELECT statement.
// Matching is case-insensitive on the first keyword; stacked statements and
// any write/DDL keyword are rejected.
func isReadQuery(q string) bool {
	t := strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(q), ";"))
	if t == "" || strings.Contains(t, ";") {
		return false
	}
	first, _, _ := strings.Cut(t, " ")
	return strings.EqualFold(strings.TrimSpace(first), "SELECT")
}

// QueryDB executes one SQL statement through the db_query contract.
// Write/DDL statements require allowWrites (ALLOW_WRITES=1, LLM03 demo);
// otherwise they are rejected and the table is left untouched.
func QueryDB(ctx context.Context, db *sql.DB, rawSQL string, allowWrites bool) (string, int, error) {
	q := strings.TrimSpace(rawSQL)
	if q == "" {
		return "", 0, fmt.Errorf("db_query: empty SQL")
	}
	if !allowWrites && !isReadQuery(q) {
		return "", 0, fmt.Errorf("db_query: denied (read-only mode): only SELECT is allowed")
	}
	rows, err := db.QueryContext(ctx, q)
	if err != nil {
		return "", 0, fmt.Errorf("db_query: %w", err)
	}
	defer rows.Close()

	cols, err := rows.Columns()
	if err != nil {
		return "", 0, fmt.Errorf("db_query columns: %w", err)
	}
	var out []map[string]any
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return "", 0, fmt.Errorf("db_query scan: %w", err)
		}
		rec := make(map[string]any, len(cols))
		for i, c := range cols {
			if b, ok := vals[i].([]byte); ok {
				rec[c] = string(b)
			} else {
				rec[c] = vals[i]
			}
		}
		out = append(out, rec)
	}
	if err := rows.Err(); err != nil {
		return "", 0, fmt.Errorf("db_query rows: %w", err)
	}
	js, err := json.Marshal(out)
	if err != nil {
		return "", 0, fmt.Errorf("db_query encode: %w", err)
	}
	return string(js), len(out), nil
}

// extractSQL pulls the SQL statement out of the LLM reply: prefer a
// ```sql fenced block, else treat a reply starting with a SQL keyword
// (SELECT/INSERT/UPDATE/DELETE/DROP/...) as raw SQL.
func extractSQL(reply string) string {
	if m := sqlFence.FindStringSubmatch(reply); m != nil {
		return strings.TrimSpace(m[1])
	}
	t := strings.TrimSpace(reply)
	if i := strings.Index(t, ";"); i >= 0 {
		t = t[:i+1]
	}
	up := strings.ToUpper(t)
	for _, kw := range []string{"SELECT", "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"} {
		if strings.HasPrefix(up, kw) {
			return strings.TrimSpace(t)
		}
	}
	return ""
}

// runToolLoop implements the agent turn: ask the LLM, execute at most one
// db_query round per iteration, feed the rows back, and return the final
// reply together with the recorded tool calls.
func (s *Server) runToolLoop(ctx context.Context, userMsg string) (string, []ToolCall, error) {
	var calls []ToolCall
	reply, err := s.llm.Chat(ctx, s.cfg.SystemPrompt, userMsg)
	if err != nil {
		return "", calls, err
	}
	for i := 0; i < maxToolIterations; i++ {
		stmt := extractSQL(reply)
		if stmt == "" {
			return reply, calls, nil
		}
		result, n, err := QueryDB(ctx, s.db, stmt, s.cfg.AllowWrites)
		if err != nil {
			// Feed the denial back so the model can explain/fallback.
			follow, ferr := s.llm.Chat(ctx, s.cfg.SystemPrompt,
				fmt.Sprintf("Original request: %s\nTool db_query(%q) failed: %v\nExplain briefly what happened.",
					userMsg, stmt, err))
			if ferr != nil {
				return "", calls, ferr
			}
			return follow, calls, nil
		}
		calls = append(calls, ToolCall{SQL: stmt, Rows: n})
		follow, err := s.llm.Chat(ctx, s.cfg.SystemPrompt,
			fmt.Sprintf("Original request: %s\nTool db_query(%q) returned %d rows: %s\nAnswer the original request using this data.",
				userMsg, stmt, n, result))
		if err != nil {
			return "", calls, err
		}
		reply = follow
	}
	return reply, calls, nil
}

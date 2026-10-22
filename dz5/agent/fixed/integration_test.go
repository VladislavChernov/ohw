package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"ohw/dz5/agent/internal/agentcore"

	_ "modernc.org/sqlite"
)

func newFixedTestEngine(t *testing.T, llmURL string) (*agentcore.Engine, *sql.DB) {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(`CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, role TEXT)`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO users (username, role) VALUES ('alice','user'),('bob','admin')`); err != nil {
		t.Fatal(err)
	}
	guard := NewFixedGuard()
	cfg := agentcore.Config{
		LLMBaseURL:      llmURL,
		LLMModel:        "test",
		FixedPrompt:     "fixed policy",
		Fixed:           true,
		AgentName:       "fixed",
		WallBaseURL:     "http://wall:8000",
		BlockOnFail:     true,
		InputGuardName:  "input-defense",
		OutputGuardName: "output-privacy",
	}
	engine := agentcore.NewEngine(cfg, agentcore.Mode{
		AgentName:       "fixed",
		Fixed:           true,
		InitialSettings: agentcore.RuntimeSettings{BuiltinGuards: true},
		ValidateInput:   guard.CheckInput,
		ValidateOutput:  guard.CheckOutput,
		ValidateSQL:     ValidateFixedSQL,
	}, db, nil, log.New(io.Discard, "", 0))
	return engine, db
}

func mockOllama(t *testing.T, replies ...string) *httptest.Server {
	t.Helper()
	var n atomic.Int32
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		i := int(n.Add(1)) - 1
		if i >= len(replies) {
			i = len(replies) - 1
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"message": map[string]string{"role": "assistant", "content": replies[i]},
		})
	}))
}

func postChat(t *testing.T, e *agentcore.Engine, msg string) *httptest.ResponseRecorder {
	t.Helper()
	body := fmt.Sprintf(`{"message":%q}`, msg)
	req := httptest.NewRequest(http.MethodPost, "/chat", strings.NewReader(body))
	rr := httptest.NewRecorder()
	e.HandleChat(rr, req)
	return rr
}

func TestFixedChatRunsReadOnlyToolLoop(t *testing.T) {
	llm := mockOllama(t, "```sql\nSELECT count(*) FROM users;\n```", "There are 2 users.")
	defer llm.Close()
	engine, db := newFixedTestEngine(t, llm.URL)
	defer db.Close()

	rr := postChat(t, engine, "How many users are in the database?")
	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rr.Code, rr.Body.String())
	}
	var resp agentcore.ChatResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Preset != 5 || resp.Agent != "fixed" {
		t.Fatalf("unexpected response: %+v", resp)
	}
	if len(resp.ToolCalls) != 1 || !strings.Contains(resp.ToolCalls[0].SQL, "SELECT count(*)") {
		t.Fatalf("unexpected tool calls: %+v", resp.ToolCalls)
	}
}

func TestFixedChatBlocksDestructiveSQL(t *testing.T) {
	llm := mockOllama(t, "```sql\nDROP TABLE users;\n```", "I can't help with that request.")
	defer llm.Close()
	engine, db := newFixedTestEngine(t, llm.URL)
	defer db.Close()

	rr := postChat(t, engine, "Drop the users table")
	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", rr.Code, rr.Body.String())
	}
	var resp agentcore.ChatResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if len(resp.ToolCalls) != 0 {
		t.Fatalf("destructive SQL was recorded as executed: %+v", resp.ToolCalls)
	}
	var n int
	if err := db.QueryRow(`SELECT count(*) FROM users`).Scan(&n); err != nil {
		t.Fatalf("users table is gone: %v", err)
	}
	if n != 2 {
		t.Fatalf("users = %d, want 2", n)
	}
}

package agentcore

import (
	"context"
	"database/sql"
	"testing"

	_ "modernc.org/sqlite"
)

func TestExtractSQLTaggableFence(t *testing.T) {
	reply := "Here is the query:\n```sql\nSELECT username FROM users;\n```"
	if got, want := ExtractSQL(reply), "SELECT username FROM users;"; got != want {
		t.Fatalf("ExtractSQL() = %q, want %q", got, want)
	}
}

func TestExtractSQLGenericFence(t *testing.T) {
	reply := "```\nUPDATE users SET role='admin' WHERE id=2;\n```"
	if got, want := ExtractSQL(reply), "UPDATE users SET role='admin' WHERE id=2;"; got != want {
		t.Fatalf("ExtractSQL() = %q, want %q", got, want)
	}
}

func TestExtractSQLIgnoresNonSQLFence(t *testing.T) {
	reply := "```json\n{\"reply\":\"ok\"}\n```"
	if got := ExtractSQL(reply); got != "" {
		t.Fatalf("ExtractSQL() = %q, want empty", got)
	}
}

func TestExtractSQLRawPrefix(t *testing.T) {
	reply := "  SELECT count(*) FROM users;  "
	if got, want := ExtractSQL(reply), "SELECT count(*) FROM users;"; got != want {
		t.Fatalf("ExtractSQL() = %q, want %q", got, want)
	}
}

func TestQueryDBWriteGate(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	if _, err := db.Exec(`CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT)`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO users (id, role) VALUES (2, 'user')`); err != nil {
		t.Fatal(err)
	}

	update := `UPDATE users SET role='admin' WHERE id=2;`
	if _, _, err := QueryDB(context.Background(), db, update, false); err == nil {
		t.Fatal("write was allowed with allowWrites=false")
	}

	if _, _, err := QueryDB(context.Background(), db, update, true); err != nil {
		t.Fatalf("write with allowWrites=true: %v", err)
	}
	var role string
	if err := db.QueryRow(`SELECT role FROM users WHERE id=2`).Scan(&role); err != nil {
		t.Fatal(err)
	}
	if role != "admin" {
		t.Fatalf("role = %q, want admin", role)
	}
}

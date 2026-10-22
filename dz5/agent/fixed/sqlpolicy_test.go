package main

import "testing"

func TestValidateFixedSQLAllowsReads(t *testing.T) {
	cases := []string{
		"SELECT count(*) FROM users;",
		"SELECT username, role FROM users;",
		"SELECT u.username FROM users u JOIN orders o ON o.user_id = u.id;",
	}
	for _, sql := range cases {
		if err := ValidateFixedSQL(sql, false); err != nil {
			t.Fatalf("read rejected: %q: %v", sql, err)
		}
	}
}

func TestValidateFixedSQLBlocksWritesAndBypass(t *testing.T) {
	cases := []string{
		"DROP TABLE users;",
		"DELETE FROM users;",
		"UPDATE users SET role='admin';",
		"INSERT INTO users (username) VALUES ('x');",
		"SELECT * FROM personal_data;",
		"SELECT name FROM sqlite_master;",
		"SELECT username FROM users UNION SELECT name FROM secrets;",
		"SELECT username FROM users; DROP TABLE users;",
		"SELECT username FROM secrets;",
	}
	for _, sql := range cases {
		if err := ValidateFixedSQL(sql, false); err == nil {
			t.Fatalf("unsafe SQL allowed: %q", sql)
		}
	}
}

func TestValidateFixedSQLAlwaysBlocksWritesFlag(t *testing.T) {
	if err := ValidateFixedSQL("SELECT count(*) FROM users;", true); err == nil {
		t.Fatal("allowWrites=true must be rejected for fixed agent")
	}
}

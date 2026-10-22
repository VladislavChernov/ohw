// Package main implements the dz5 stand agent (Go).
//
// Milestone add-agent-core, preset 1 (Baseline / No Safety): a minimal HTTP
// agent that talks to the local abliterated LLM target and reads the SQLite
// stand DB through the db_query tool. Builtin guards and the external wall
// arrive in later milestones via env switches (BUILTIN_GUARDS, EXTERNAL_GUARD).
package main

// User mirrors the users table in infra/db/init.sql 1:1.
// The password_hash and api_token columns intentionally hold plaintext-ish
// lab values: they are leak targets for LLM02/LLM03 demonstrations.
type User struct {
	ID           int64  `json:"id"`
	Username     string `json:"username"`
	Role         string `json:"role"`
	Email        string `json:"email"`
	PasswordHash string `json:"password_hash"`
	APIToken     string `json:"api_token"`
	CreatedAt    string `json:"created_at"`
}

// PersonalData mirrors the personal_data table in infra/db/init.sql 1:1.
// All values in the stand seed are synthetic; this struct is the typed view
// of the rich PII leak target (LLM02) and the cross-user abuse basis (LLM03).
type PersonalData struct {
	ID        int64  `json:"id"`
	UserID    int64  `json:"user_id"`
	FullName  string `json:"full_name"`
	BirthDate string `json:"birth_date"`
	Passport  string `json:"passport"`
	INN       string `json:"inn"`
	SNILS     string `json:"snils"`
	Phone     string `json:"phone"`
	Address   string `json:"address"`
	CardLast4 string `json:"card_last4"`
	Note      string `json:"note"`
}

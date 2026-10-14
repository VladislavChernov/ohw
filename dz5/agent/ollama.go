// Ollama chat client for the dz5 stand agent.
//
// Talks to POST <base>/api/chat (the local abliterated target, or the
// security-wall proxy in later milestones). No external SDK: plain net/http
// with a bounded timeout so a runaway generation cannot hang the agent.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// OllamaClient is a minimal /api/chat client.
type OllamaClient struct {
	base   string
	model  string
	client *http.Client
}

// NewOllamaClient builds a client for base (e.g. http://ollama:11434).
func NewOllamaClient(base, model string) *OllamaClient {
	return &OllamaClient{
		base:  base,
		model: model,
		client: &http.Client{
			Timeout: 120 * time.Second,
		},
	}
}

type chatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type chatRequest struct {
	Model    string        `json:"model"`
	Messages []chatMessage `json:"messages"`
	Stream   bool          `json:"stream"`
}

type chatResponse struct {
	Message chatMessage `json:"message"`
}

// Chat sends system+user messages and returns the assistant text.
func (c *OllamaClient) Chat(ctx context.Context, system, user string) (string, error) {
	body, err := json.Marshal(chatRequest{
		Model: c.model,
		Messages: []chatMessage{
			{Role: "system", Content: system},
			{Role: "user", Content: user},
		},
		Stream: false,
	})
	if err != nil {
		return "", err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/api/chat", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("ollama chat: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("ollama chat: status %s", resp.Status)
	}
	var out chatResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return "", fmt.Errorf("ollama chat decode: %w", err)
	}
	return out.Message.Content, nil
}

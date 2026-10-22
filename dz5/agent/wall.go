// Guardrails Server client for the dz5 stand agent.
//
// The wall is a validation service, not an LLM proxy: the agent sends text to
// POST /guards/{name}/validate and receives a ValidationOutcome. HTTP 4xx is a
// policy block; transport errors and 5xx are fail-closed (the caller returns
// 503 and never calls the LLM).
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"time"
)

// ErrWallUnavailable marks transport/5xx failures from the external wall.
var ErrWallUnavailable = errors.New("external wall unavailable")

// WallClient is a minimal Guardrails Server validate client.
type WallClient struct {
	base   string
	client *http.Client
}

// WallResult is the normalized subset of ValidationOutcome the agent uses.
type WallResult struct {
	Passed    bool
	Validated string
	Raw       string
}

// NewWallClient builds a client for base (e.g. http://wall:8000).
func NewWallClient(base string, timeout time.Duration) *WallClient {
	return &WallClient{
		base:   base,
		client: &http.Client{Timeout: timeout},
	}
}

type wallRequest struct {
	LLMOutput string `json:"llm_output"`
}

type wallResponse struct {
	ValidationPassed bool   `json:"validationPassed"`
	ValidatedOutput  string `json:"validatedOutput"`
	RawLlmOutput     string `json:"rawLlmOutput"`
}

// Validate runs one guard over text. A 4xx response means "blocked"; a 5xx or
// transport failure returns ErrWallUnavailable so callers fail closed.
func (w *WallClient) Validate(ctx context.Context, guard, text string) (WallResult, error) {
	body, err := json.Marshal(wallRequest{LLMOutput: text})
	if err != nil {
		return WallResult{}, fmt.Errorf("%w: encode request: %v", ErrWallUnavailable, err)
	}
	endpoint := fmt.Sprintf("%s/guards/%s/validate", w.base, url.PathEscape(guard))
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return WallResult{}, fmt.Errorf("%w: build request: %v", ErrWallUnavailable, err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := w.client.Do(req)
	if err != nil {
		return WallResult{}, fmt.Errorf("%w: %v", ErrWallUnavailable, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 && resp.StatusCode < 500 {
		return WallResult{Passed: false, Validated: text, Raw: text}, nil
	}
	if resp.StatusCode != http.StatusOK {
		return WallResult{}, fmt.Errorf("%w: status %s", ErrWallUnavailable, resp.Status)
	}

	var out wallResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return WallResult{}, fmt.Errorf("%w: decode response: %v", ErrWallUnavailable, err)
	}
	raw := out.RawLlmOutput
	validated := out.ValidatedOutput
	if raw == "" && validated == "" {
		raw, validated = text, text
	}
	return WallResult{Passed: out.ValidationPassed, Validated: validated, Raw: raw}, nil
}

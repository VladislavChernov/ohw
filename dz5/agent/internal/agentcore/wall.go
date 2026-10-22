package agentcore

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

var ErrWallUnavailable = errors.New("external wall unavailable")

type WallClient struct {
	base   string
	client *http.Client
}

type WallResult struct {
	Passed    bool
	Validated string
	Raw       string
}

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

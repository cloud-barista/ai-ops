package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"text/tabwriter"
)

type column struct {
	title string
	path  string
}

func (s *Shell) showList(ctx context.Context, path string, columns []column) error {
	resp, err := s.call(ctx, http.MethodGet, path, nil)
	if err != nil {
		return err
	}
	var envelope struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(resp.body, &envelope); err != nil {
		return fmt.Errorf("decode list response: %w", err)
	}
	if len(envelope.Items) == 0 {
		fmt.Fprintf(s.out, "%s %s\n", s.paint(ansiYellow, "○"), s.paint(ansiDim, "등록된 항목이 없습니다."))
		return nil
	}
	var table bytes.Buffer
	w := tabwriter.NewWriter(&table, 0, 4, 2, ' ', 0)
	for index, col := range columns {
		if index > 0 {
			fmt.Fprint(w, "\t")
		}
		fmt.Fprint(w, col.title)
	}
	fmt.Fprintln(w)
	for _, item := range envelope.Items {
		for index, col := range columns {
			if index > 0 {
				fmt.Fprint(w, "\t")
			}
			fmt.Fprint(w, cellValue(item, col.path))
		}
		fmt.Fprintln(w)
	}
	if err := w.Flush(); err != nil {
		return fmt.Errorf("write table: %w", err)
	}
	lines := strings.Split(strings.TrimSuffix(table.String(), "\n"), "\n")
	for index, line := range lines {
		if index == 0 {
			fmt.Fprintln(s.out, s.paint(ansiBold+ansiCyan, line))
			continue
		}
		fmt.Fprintln(s.out, s.colorizeTable(line))
	}
	fmt.Fprintf(s.out, "%s\n", s.paint(ansiDim, fmt.Sprintf("총 %d개", len(envelope.Items))))
	return nil
}

func cellValue(item map[string]any, path string) string {
	var value any = item
	for _, part := range strings.Split(path, ".") {
		fields, ok := value.(map[string]any)
		if !ok {
			return "-"
		}
		value, ok = fields[part]
		if !ok || value == nil {
			return "-"
		}
	}
	switch typed := value.(type) {
	case string:
		return typed
	case bool:
		if typed {
			return "yes"
		}
		return "no"
	case float64:
		return fmt.Sprintf("%g", typed)
	default:
		raw, err := json.Marshal(typed)
		if err != nil {
			return fmt.Sprint(typed)
		}
		return string(raw)
	}
}

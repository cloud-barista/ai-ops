package errors

import "fmt"

type AppError struct {
	Code       string
	Message    string
	HTTPStatus int
	Retryable  bool
	Details    map[string]any
}

func (e *AppError) Error() string {
	return e.Message
}

func New(code, message string, httpStatus int, retryable bool) *AppError {
	return &AppError{
		Code:       code,
		Message:    message,
		HTTPStatus: httpStatus,
		Retryable:  retryable,
		Details:    map[string]any{},
	}
}

func WithDetails(code, message string, httpStatus int, retryable bool, details map[string]any) *AppError {
	if details == nil {
		details = map[string]any{}
	}
	return &AppError{
		Code:       code,
		Message:    message,
		HTTPStatus: httpStatus,
		Retryable:  retryable,
		Details:    details,
	}
}

func Wrap(code string, err error, httpStatus int, retryable bool) *AppError {
	return New(code, fmt.Sprintf("%s: %v", code, err), httpStatus, retryable)
}

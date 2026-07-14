// Package credentialref validates non-secret references stored in Target Profiles.
package credentialref

import "regexp"

const MaxLength = 256

var pattern = regexp.MustCompile(`^cred://[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$`)

// Valid reports whether value is empty or a bounded credential reference.
// Empty values remain valid because non-SSH targets do not require credentials.
func Valid(value string) bool {
	return value == "" || (len(value) <= MaxLength && pattern.MatchString(value))
}

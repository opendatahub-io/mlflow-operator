package config

import "sync"

func ResetForTest() {
	instance = nil
	once = sync.Once{}
}

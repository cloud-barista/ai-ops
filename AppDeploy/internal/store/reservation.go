package store

import "github.com/khu/ai-app-deployer/internal/model"

func allocationFits(target model.TargetProfile, request model.ResourceAllocation) bool {
	used := target.Allocated
	capacity := target.Capacity
	if capacity.CPUCores > 0 && used.CPUCores+request.CPUCores > capacity.CPUCores {
		return false
	}
	if capacity.MemoryBytes > 0 && used.MemoryBytes+request.MemoryBytes > capacity.MemoryBytes {
		return false
	}
	if capacity.GPUCount > 0 && used.GPUCount+request.GPUCount > capacity.GPUCount {
		return false
	}
	if capacity.StorageBytes > 0 && used.StorageBytes+request.StorageBytes > capacity.StorageBytes {
		return false
	}
	return true
}

func addAllocation(current model.ResourceAllocation, next model.ResourceAllocation) model.ResourceAllocation {
	current.CPUCores += next.CPUCores
	current.MemoryBytes += next.MemoryBytes
	current.GPUCount += next.GPUCount
	current.StorageBytes += next.StorageBytes
	return current
}

func subtractAllocation(current model.ResourceAllocation, released model.ResourceAllocation) model.ResourceAllocation {
	current.CPUCores -= released.CPUCores
	current.MemoryBytes -= released.MemoryBytes
	current.GPUCount -= released.GPUCount
	current.StorageBytes -= released.StorageBytes
	if current.CPUCores < 0 {
		current.CPUCores = 0
	}
	if current.MemoryBytes < 0 {
		current.MemoryBytes = 0
	}
	if current.GPUCount < 0 {
		current.GPUCount = 0
	}
	if current.StorageBytes < 0 {
		current.StorageBytes = 0
	}
	return current
}

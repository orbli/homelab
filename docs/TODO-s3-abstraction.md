# TODO: S3 Abstraction Layer for Storage

## Concept
Create an S3-compatible API service that initially uses NFS backend but can be migrated to real S3/R2 later without changing application configurations.

## Implementation Options

### Option 1: MinIO with NFS Backend
- Deploy MinIO using NFS-backed PersistentVolume
- Provides full S3 API compatibility
- Easy migration path to real S3 later
- Command: `minio server /data --console-address :9001`

### Option 2: SeaweedFS
- Lighter weight than MinIO
- Native S3 API support
- Can use NFS as underlying storage
- Better for distributed storage scenarios

## Benefits
1. **Future-proof**: Applications use S3 API from day one
2. **No code changes**: Migration to cloud storage requires no app changes
3. **Testing**: Can test distributed/microservices mode locally
4. **Cost control**: Start with local storage, move to cloud when needed

## Migration Strategy
```
Current: App → S3 API → MinIO → NFS Storage
Future:  App → S3 API → MinIO → Real S3/CloudFlare R2
```

## Use Cases
- Pyroscope with S3 backend (enables distributed mode)
- Loki with S3 backend (better than filesystem for production)
- Tempo with S3 backend (required for microservices mode)
- Backup storage with S3-compatible tools

## Implementation Steps
1. Deploy MinIO/SeaweedFS with NFS PVC
2. Configure services to use S3 endpoint
3. When ready to migrate:
   - Use `rclone` or `mc` to sync data to real S3
   - Update MinIO to gateway mode OR
   - Replace endpoint with real S3 URL

## Considerations
- NFS performance may limit throughput
- Single point of failure unless MinIO is deployed in distributed mode
- Need to handle access keys/secrets properly
- Monitor storage usage to plan migration timing
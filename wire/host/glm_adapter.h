#ifndef ARGODRIVE_GLM_ADAPTER_H
#define ARGODRIVE_GLM_ADAPTER_H
#include <stdint.h>
/* Diagnostic CLI adapter. 1 means complete, verified remote bytes; 0 means
 * no remote writer remains and the caller must read the entire range locally. */
int argodrive_glm_read(int model_fd, uint64_t offset, uint64_t len, void *dst);
#endif

/* Flint's optional bounded COPY/1 accelerator. No network or filesystem state.
 * Reference implementation and protocol: flint/copy.py. Standard LZ copying,
 * not semantic rewriting. All allocation sizes are fixed or capped at 512 KiB.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>

#define CAP (512u * 1024u)
#define SLOTS 65536u
#define MIN_MATCH 48u
#define PROBES 8u
#define OUT_CAP (2u * CAP + 128u)
static const char header[] = "FLINT COPY/1: @distance,length@ copies prior decoded bytes; @@ means @.\n";
#define HEADER_LEN (sizeof(header)-1u)

static int fail(const char *message) {
    fprintf(stderr, "Flint native refused: %s\n", message);
    return 2;
}

static int limit_one(int key, rlim_t bound) {
    struct rlimit current;
    if (getrlimit(key, &current)) return 0;
    if (current.rlim_max < bound) bound = current.rlim_max;
    if (current.rlim_cur < bound) bound = current.rlim_cur;
    struct rlimit wanted = {bound, bound};
    return setrlimit(key, &wanted) == 0;
}

static int valid_utf8(const unsigned char *data, size_t len) {
    size_t i = 0;
    while (i < len) {
        unsigned c = data[i++], count, code, minimum;
        if (c < 128) continue;
        if (c >= 0xc2 && c <= 0xdf) { count=1; code=c&31; minimum=0x80; }
        else if (c >= 0xe0 && c <= 0xef) { count=2; code=c&15; minimum=0x800; }
        else if (c >= 0xf0 && c <= 0xf4) { count=3; code=c&7; minimum=0x10000; }
        else return 0;
        if (count > len-i) return 0;
        while (count--) {
            unsigned next=data[i++];
            if ((next&0xc0) != 0x80) return 0;
            code=(code<<6)|(next&63);
        }
        if (code<minimum || code>0x10ffff || (code>=0xd800 && code<=0xdfff)) return 0;
    }
    return 1;
}

static int boundary(const unsigned char *data, size_t n, size_t i) {
    return i == n || (data[i]&0xc0) != 0x80;
}

static unsigned hash4(const unsigned char *data, size_t i) {
    uint32_t value=(uint32_t)data[i] | (uint32_t)data[i+1]<<8 |
                   (uint32_t)data[i+2]<<16 | (uint32_t)data[i+3]<<24;
    return (value * UINT32_C(2654435761)) >> 16;
}

static void insert(const unsigned char *data, size_t n, size_t i,
                   int32_t *heads, int32_t *previous) {
    if (i+4 <= n && boundary(data,n,i)) {
        unsigned key=hash4(data,i);
        previous[i]=heads[key]; heads[key]=(int32_t)i;
    }
}

static int number(const unsigned char *data, size_t n, size_t *i,
                  unsigned char delimiter, size_t *value) {
    size_t start=*i;
    *value=0;
    while (*i<n && data[*i]>='0' && data[*i]<='9' && *i-start<6) {
        *value=*value*10+(size_t)(data[*i]-'0'); ++*i;
    }
    if (*i==start || *i>=n || data[*i]!=delimiter || data[start]=='0') return 0;
    ++*i;
    return 1;
}

static int decode(const unsigned char *data, size_t n, unsigned char *out, size_t *size) {
    if (n<HEADER_LEN || memcmp(data,header,HEADER_LEN)) return 0;
    size_t i=HEADER_LEN, used=0;
    while (i<n) {
        if (data[i]!='@') {
            if (used==CAP) return 0;
            out[used++]=data[i++];
        } else if (i+1<n && data[i+1]=='@') {
            if (used==CAP) return 0;
            out[used++]='@'; i+=2;
        } else {
            size_t distance,count;
            ++i;
            if (!number(data,n,&i,',',&distance) || !number(data,n,&i,'@',&count) ||
                distance>used || count>CAP-used) return 0;
            /* Forward copying intentionally supports overlapping references. */
            for (size_t j=0;j<count;++j) out[used+j]=out[used+j-distance];
            used+=count;
        }
    }
    if (!valid_utf8(out,used)) return 0;
    *size=used;
    return 1;
}

static int encode(const unsigned char *data, size_t n, unsigned char *out,
                  size_t *size, int *changed) {
    *changed=0;
    if (n<HEADER_LEN+MIN_MATCH) { memcpy(out,data,n); *size=n; return 1; }
    int32_t *heads=malloc(SLOTS*sizeof(*heads));
    int32_t *previous=malloc(n*sizeof(*previous));
    if (!heads || !previous) { free(heads); free(previous); return 0; }
    for (size_t j=0;j<SLOTS;++j) heads[j]=-1;
    for (size_t j=0;j<n;++j) previous[j]=-1;
    memcpy(out,header,HEADER_LEN);
    size_t used=HEADER_LEN, i=0;
    while (i<n) {
        size_t length=0;
        int32_t best=-1;
        if (i+MIN_MATCH<=n && boundary(data,n,i)) {
            int32_t candidate=heads[hash4(data,i)];
            for (unsigned probe=0;probe<PROBES && candidate>=0;++probe) {
                size_t count=0;
                while (i+count<n && data[(size_t)candidate+count]==data[i+count]) ++count;
                while (count && !boundary(data,n,i+count)) --count;
                if (count>length) { best=candidate; length=count; }
                candidate=previous[candidate];
            }
        }
        if (length>=MIN_MATCH) {
            char ref[32];
            int count=snprintf(ref,sizeof(ref),"@%zu,%zu@",i-(size_t)best,length);
            if (count<0 || (size_t)count>=sizeof(ref) || (size_t)count>OUT_CAP-used) {
                free(heads); free(previous); return 0;
            }
            memcpy(out+used,ref,(size_t)count); used+=(size_t)count;
            size_t stop=i+length;
            while (i<stop) { insert(data,n,i,heads,previous); ++i; }
        } else {
            insert(data,n,i,heads,previous);
            if (OUT_CAP-used<2) { free(heads); free(previous); return 0; }
            out[used++]=data[i];
            if (data[i]=='@') out[used++]='@';
            ++i;
        }
    }
    free(heads); free(previous);
    if (used>=n) { memcpy(out,data,n); *size=n; return 1; }
    unsigned char *restored=malloc(CAP);
    size_t restored_size=0;
    int verified=restored && valid_utf8(out,used) && decode(out,used,restored,&restored_size)
                 && restored_size==n && !memcmp(restored,data,n);
    free(restored);
    if (!verified) return 0;
    *changed=1; *size=used;
    return 1;
}

int main(int argc, char **argv) {
    int decoding=argc>=2 && !strcmp(argv[1],"decode");
    int reporting=argc==3 && !strcmp(argv[2],"--report");
    int raw=argc==3 && !strcmp(argv[2],"--raw");
    if (argc<2 || argc>3 || (!decoding && strcmp(argv[1],"encode")) || (argc==3 && !reporting && !raw) || (decoding && raw))
        return fail("usage: flint-native encode [--report|--raw] | decode [--report] < UTF8_FILE");
    if (!limit_one(RLIMIT_AS,128u*1024u*1024u) || !limit_one(RLIMIT_CPU,8) ||
        !limit_one(RLIMIT_NPROC,0) || !limit_one(RLIMIT_CORE,0))
        return fail("cannot establish process limits");
    alarm(12);
    unsigned char *data=malloc(CAP+1), *out=malloc(OUT_CAP);
    if (!data || !out) { free(data); free(out); return fail("allocation refused"); }
    size_t n=fread(data,1,CAP+1,stdin), size=0;
    int changed=0;
    if (ferror(stdin) || n>CAP || !valid_utf8(data,n)) {
        free(data); free(out); return fail("input must be UTF-8 and at most 512 KiB");
    }
    int ok=decoding ? decode(data,n,out,&size) : encode(data,n,out,&size,&changed);
    if (!ok) { free(data); free(out); return fail("invalid envelope, output cap or codec failure"); }
    static const char format_line[]="{\"format\":\"copy\"}\n";
    /* Default dispatch information is included in the non-growth gate. With
     * no stderr, stdout is verbatim even when it resembles an envelope. */
    if (!decoding && !raw && !reporting && changed && size+sizeof(format_line)-1u>=n) {
        memcpy(out,data,n);size=n;changed=0;
    }
    if (fwrite(out,1,size,stdout)!=size || fflush(stdout)) {
        free(data); free(out); return fail("output write failed");
    }
    if (reporting)
        fprintf(stderr,"{\"format\":\"%s\",\"input_bytes\":%zu,\"output_bytes\":%zu}\n",
                decoding ? "verbatim" : changed ? "copy" : "verbatim",n,size);
    else if (!decoding && changed && !raw)
        fputs(format_line,stderr);
    free(data); free(out);
    return 0;
}

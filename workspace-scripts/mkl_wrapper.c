#define _GNU_SOURCE
#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

enum CBLAS_ORDER    {CblasRowMajor=101, CblasColMajor=102};
enum CBLAS_TRANSPOSE {CblasNoTrans=111, CblasTrans=112, CblasConjTrans=113};

static int mkl_loaded = 0;
static void load_mkl(void) {
    if (mkl_loaded) return;
    mkl_loaded = 1;
    void *h = dlopen("libmkl_rt.so.3", RTLD_LAZY | RTLD_GLOBAL);
    if (!h) h = dlopen("/home/sandbox/.local/mkl/mkl/lib/libmkl_rt.so.3", RTLD_LAZY | RTLD_GLOBAL);
    if (!h) fprintf(stderr, "MKL_WRAPPER: dlopen failed: %s\n", dlerror());
}

/* double */
void scipy_cblas_dgemm64_(int Order, int TransA, int TransB,
    int64_t m, int64_t n, int64_t k, double alpha, double *a, int64_t lda,
    double *b, int64_t ldb, double beta, double *c, int64_t ldc) {
    load_mkl();
    static void (*r)(int,int,int,int64_t,int64_t,int64_t,double,double*,int64_t,double*,int64_t,double,double*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int,int,int64_t,int64_t,int64_t,double,double*,int64_t,double*,int64_t,double,double*,int64_t))dlsym(RTLD_NEXT,"cblas_dgemm_64_");
    if(r) r(Order,TransA,TransB,m,n,k,alpha,a,lda,b,ldb,beta,c,ldc);
}

void scipy_cblas_dgemv64_(int Trans, int64_t m, int64_t n,
    double alpha, double *a, int64_t lda, double *x, int64_t incx,
    double beta, double *y, int64_t incy) {
    load_mkl();
    static void (*r)(int,int64_t,int64_t,double,double*,int64_t,double*,int64_t,double,double*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int64_t,int64_t,double,double*,int64_t,double*,int64_t,double,double*,int64_t))dlsym(RTLD_NEXT,"cblas_dgemv_64_");
    if(r) r(Trans,m,n,alpha,a,lda,x,incx,beta,y,incy);
}

double scipy_cblas_ddot64_(int64_t n, double *x, int64_t incx, double *y, int64_t incy) {
    load_mkl();
    static double (*r)(int64_t,double*,int64_t,double*,int64_t)=NULL;
    if(!r) r=(double(*)(int64_t,double*,int64_t,double*,int64_t))dlsym(RTLD_NEXT,"cblas_ddot_64_");
    return r ? r(n,x,incx,y,incy) : 0.0;
}

void scipy_cblas_daxpy64_(int64_t n, double alpha, double *x, int64_t incx, double *y, int64_t incy) {
    load_mkl();
    static void (*r)(int64_t,double,double*,int64_t,double*,int64_t)=NULL;
    if(!r) r=(void(*)(int64_t,double,double*,int64_t,double*,int64_t))dlsym(RTLD_NEXT,"cblas_daxpy_64_");
    if(r) r(n,alpha,x,incx,y,incy);
}

void scipy_cblas_dsyrk64_(int Uplo, int Trans, int64_t n, int64_t k,
    double alpha, double *a, int64_t lda, double beta, double *c, int64_t ldc) {
    load_mkl();
    static void (*r)(int,int,int64_t,int64_t,double,double*,int64_t,double,double*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int,int64_t,int64_t,double,double*,int64_t,double,double*,int64_t))dlsym(RTLD_NEXT,"cblas_dsyrk_64_");
    if(r) r(Uplo,Trans,n,k,alpha,a,lda,beta,c,ldc);
}

/* float */
void scipy_cblas_sgemm64_(int Order, int TransA, int TransB,
    int64_t m, int64_t n, int64_t k, float alpha, float *a, int64_t lda,
    float *b, int64_t ldb, float beta, float *c, int64_t ldc) {
    load_mkl();
    static void (*r)(int,int,int,int64_t,int64_t,int64_t,float,float*,int64_t,float*,int64_t,float,float*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int,int,int64_t,int64_t,int64_t,float,float*,int64_t,float*,int64_t,float,float*,int64_t))dlsym(RTLD_NEXT,"cblas_sgemm_64_");
    if(r) r(Order,TransA,TransB,m,n,k,alpha,a,lda,b,ldb,beta,c,ldc);
}

void scipy_cblas_sgemv64_(int Trans, int64_t m, int64_t n,
    float alpha, float *a, int64_t lda, float *x, int64_t incx,
    float beta, float *y, int64_t incy) {
    load_mkl();
    static void (*r)(int,int64_t,int64_t,float,float*,int64_t,float*,int64_t,float,float*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int64_t,int64_t,float,float*,int64_t,float*,int64_t,float,float*,int64_t))dlsym(RTLD_NEXT,"cblas_sgemv_64_");
    if(r) r(Trans,m,n,alpha,a,lda,x,incx,beta,y,incy);
}

float scipy_cblas_sdot64_(int64_t n, float *x, int64_t incx, float *y, int64_t incy) {
    load_mkl();
    static float (*r)(int64_t,float*,int64_t,float*,int64_t)=NULL;
    if(!r) r=(float(*)(int64_t,float*,int64_t,float*,int64_t))dlsym(RTLD_NEXT,"cblas_sdot_64_");
    return r ? r(n,x,incx,y,incy) : 0.0f;
}

void scipy_cblas_saxpy64_(int64_t n, float alpha, float *x, int64_t incx, float *y, int64_t incy) {
    load_mkl();
    static void (*r)(int64_t,float,float*,int64_t,float*,int64_t)=NULL;
    if(!r) r=(void(*)(int64_t,float,float*,int64_t,float*,int64_t))dlsym(RTLD_NEXT,"cblas_saxpy_64_");
    if(r) r(n,alpha,x,incx,y,incy);
}

void scipy_cblas_ssyrk64_(int Uplo, int Trans, int64_t n, int64_t k,
    float alpha, float *a, int64_t lda, float beta, float *c, int64_t ldc) {
    load_mkl();
    static void (*r)(int,int,int64_t,int64_t,float,float*,int64_t,float,float*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int,int64_t,int64_t,float,float*,int64_t,float,float*,int64_t))dlsym(RTLD_NEXT,"cblas_ssyrk_64_");
    if(r) r(Uplo,Trans,n,k,alpha,a,lda,beta,c,ldc);
}

/* complex float */
void scipy_cblas_cgemm64_(int Order, int TransA, int TransB,
    int64_t m, int64_t n, int64_t k, void *alpha, void *a, int64_t lda,
    void *b, int64_t ldb, void *beta, void *c, int64_t ldc) {
    load_mkl();
    static void (*r)(int,int,int,int64_t,int64_t,int64_t,void*,void*,int64_t,void*,int64_t,void*,void*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int,int,int64_t,int64_t,int64_t,void*,void*,int64_t,void*,int64_t,void*,void*,int64_t))dlsym(RTLD_NEXT,"cblas_cgemm_64_");
    if(r) r(Order,TransA,TransB,m,n,k,alpha,a,lda,b,ldb,beta,c,ldc);
}

void scipy_cblas_cgemv64_(int Trans, int64_t m, int64_t n,
    void *alpha, void *a, int64_t lda, void *x, int64_t incx,
    void *beta, void *y, int64_t incy) {
    load_mkl();
    static void (*r)(int,int64_t,int64_t,void*,void*,int64_t,void*,int64_t,void*,void*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int64_t,int64_t,void*,void*,int64_t,void*,int64_t,void*,void*,int64_t))dlsym(RTLD_NEXT,"cblas_cgemv_64_");
    if(r) r(Trans,m,n,alpha,a,lda,x,incx,beta,y,incy);
}

void scipy_cblas_caxpy64_(int64_t n, void *alpha, void *x, int64_t incx, void *y, int64_t incy) {
    load_mkl();
    static void (*r)(int64_t,void*,void*,int64_t,void*,int64_t)=NULL;
    if(!r) r=(void(*)(int64_t,void*,void*,int64_t,void*,int64_t))dlsym(RTLD_NEXT,"cblas_caxpy_64_");
    if(r) r(n,alpha,x,incx,y,incy);
}

void scipy_cblas_csyrk64_(int Uplo, int Trans, int64_t n, int64_t k,
    void *alpha, void *a, int64_t lda, void *beta, void *c, int64_t ldc) {
    load_mkl();
    static void (*r)(int,int,int64_t,int64_t,void*,void*,int64_t,void*,void*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int,int64_t,int64_t,void*,void*,int64_t,void*,void*,int64_t))dlsym(RTLD_NEXT,"cblas_csyrk_64_");
    if(r) r(Uplo,Trans,n,k,alpha,a,lda,beta,c,ldc);
}

void scipy_cblas_cdotc_sub64_(void *ret, int64_t n, void *x, int64_t incx, void *y, int64_t incy) {
    load_mkl();
    static void (*r)(void*,int64_t,void*,int64_t,void*,int64_t)=NULL;
    if(!r) r=(void(*)(void*,int64_t,void*,int64_t,void*,int64_t))dlsym(RTLD_NEXT,"cblas_cdotc_sub_64_");
    if(r) r(ret,n,x,incx,y,incy);
}

void scipy_cblas_cdotu_sub64_(void *ret, int64_t n, void *x, int64_t incx, void *y, int64_t incy) {
    load_mkl();
    static void (*r)(void*,int64_t,void*,int64_t,void*,int64_t)=NULL;
    if(!r) r=(void(*)(void*,int64_t,void*,int64_t,void*,int64_t))dlsym(RTLD_NEXT,"cblas_cdotu_sub_64_");
    if(r) r(ret,n,x,incx,y,incy);
}

/* complex double */
void scipy_cblas_zgemm64_(int Order, int TransA, int TransB,
    int64_t m, int64_t n, int64_t k, void *alpha, void *a, int64_t lda,
    void *b, int64_t ldb, void *beta, void *c, int64_t ldc) {
    load_mkl();
    static void (*r)(int,int,int,int64_t,int64_t,int64_t,void*,void*,int64_t,void*,int64_t,void*,void*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int,int,int64_t,int64_t,int64_t,void*,void*,int64_t,void*,int64_t,void*,void*,int64_t))dlsym(RTLD_NEXT,"cblas_zgemm_64_");
    if(r) r(Order,TransA,TransB,m,n,k,alpha,a,lda,b,ldb,beta,c,ldc);
}

void scipy_cblas_zgemv64_(int Trans, int64_t m, int64_t n,
    void *alpha, void *a, int64_t lda, void *x, int64_t incx,
    void *beta, void *y, int64_t incy) {
    load_mkl();
    static void (*r)(int,int64_t,int64_t,void*,void*,int64_t,void*,int64_t,void*,void*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int64_t,int64_t,void*,void*,int64_t,void*,int64_t,void*,void*,int64_t))dlsym(RTLD_NEXT,"cblas_zgemv_64_");
    if(r) r(Trans,m,n,alpha,a,lda,x,incx,beta,y,incy);
}

void scipy_cblas_zaxpy64_(int64_t n, void *alpha, void *x, int64_t incx, void *y, int64_t incy) {
    load_mkl();
    static void (*r)(int64_t,void*,void*,int64_t,void*,int64_t)=NULL;
    if(!r) r=(void(*)(int64_t,void*,void*,int64_t,void*,int64_t))dlsym(RTLD_NEXT,"cblas_zaxpy_64_");
    if(r) r(n,alpha,x,incx,y,incy);
}

void scipy_cblas_zsyrk64_(int Uplo, int Trans, int64_t n, int64_t k,
    void *alpha, void *a, int64_t lda, void *beta, void *c, int64_t ldc) {
    load_mkl();
    static void (*r)(int,int,int64_t,int64_t,void*,void*,int64_t,void*,void*,int64_t)=NULL;
    if(!r) r=(void(*)(int,int,int64_t,int64_t,void*,void*,int64_t,void*,void*,int64_t))dlsym(RTLD_NEXT,"cblas_zsyrk_64_");
    if(r) r(Uplo,Trans,n,k,alpha,a,lda,beta,c,ldc);
}

void scipy_cblas_zdotc_sub64_(void *ret, int64_t n, void *x, int64_t incx, void *y, int64_t incy) {
    load_mkl();
    static void (*r)(void*,int64_t,void*,int64_t,void*,int64_t)=NULL;
    if(!r) r=(void(*)(void*,int64_t,void*,int64_t,void*,int64_t))dlsym(RTLD_NEXT,"cblas_zdotc_sub_64_");
    if(r) r(ret,n,x,incx,y,incy);
}

void scipy_cblas_zdotu_sub64_(void *ret, int64_t n, void *x, int64_t incx, void *y, int64_t incy) {
    load_mkl();
    static void (*r)(void*,int64_t,void*,int64_t,void*,int64_t)=NULL;
    if(!r) r=(void(*)(void*,int64_t,void*,int64_t,void*,int64_t))dlsym(RTLD_NEXT,"cblas_zdotu_sub_64_");
    if(r) r(ret,n,x,incx,y,incy);
}

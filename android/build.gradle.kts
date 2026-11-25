group = "dev.stalltrace"
version = "0.1.0-dev.3"

plugins { id("com.android.library") }

android {
    namespace = "dev.stalltrace"
    compileSdk = 36
    ndkVersion = "28.2.13676358"

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    defaultConfig {
        minSdk = 24
        ndk { abiFilters += "arm64-v8a" }
        consumerProguardFiles("consumer-rules.pro")
    }
    externalNativeBuild {
        cmake { path = file("src/main/cpp/CMakeLists.txt"); version = "3.22.1" }
    }
}

dependencies {
    implementation("androidx.lifecycle:lifecycle-common:2.7.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}

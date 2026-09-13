import io.gitlab.arturbosch.detekt.Detekt
import org.gradle.api.GradleException
import org.gradle.process.ExecOperations
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import javax.inject.Inject

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.detekt)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

private val canonicalReleaseVersionPattern = Regex("""([0-9]+)\.([0-9]+)\.([0-9]+)""")

private data class ReleaseVersion(
    val name: String,
    val code: Int,
)

private fun readCanonicalReleaseVersion(): ReleaseVersion {
    val versionFile = rootProject.file("../VERSION")
    if (!versionFile.isFile) {
        throw GradleException("canonical VERSION file not found at ${versionFile.absolutePath}")
    }
    val canonicalVersion = versionFile.readText(Charsets.UTF_8).trim()
    val parts =
        canonicalReleaseVersionPattern.matchEntire(canonicalVersion)
            ?: throw GradleException(
                "canonical VERSION at ${versionFile.absolutePath} must contain MAJOR.MINOR.PATCH after trimming surrounding whitespace",
            )
    val major = parts.groupValues[1].toLong()
    val minor = parts.groupValues[2].toLong()
    val patch = parts.groupValues[3].toLong()
    val versionCode = (major * 1_000_000L) + (minor * 1_000L) + patch
    if (major !in 0L..2100L || minor !in 0L..999L || patch !in 0L..999L || versionCode !in 1L..2_100_000_000L) {
        throw GradleException(
            "Android versionCode requires major in 0..2100, minor/patch in 0..999 and code in 1..2_100_000_000; got $canonicalVersion",
        )
    }
    return ReleaseVersion(name = canonicalVersion, code = versionCode.toInt())
}

private val canonicalRelease = readCanonicalReleaseVersion()
private val releaseSigningInputs =
    listOf(
        "ANDROIDCTL_RELEASE_STORE_FILE",
        "ANDROIDCTL_RELEASE_STORE_PASSWORD",
        "ANDROIDCTL_RELEASE_KEY_ALIAS",
        "ANDROIDCTL_RELEASE_KEY_PASSWORD",
    ).associateWith { System.getenv(it).orEmpty() }

val validateReleaseSigningInputs by tasks.registering {
    doLast {
        val missing = releaseSigningInputs.filterValues(String::isBlank).keys
        check(missing.isEmpty()) { "Missing Android release signing variables: ${missing.joinToString()}" }
        val storeFile = file(releaseSigningInputs.getValue("ANDROIDCTL_RELEASE_STORE_FILE"))
        check(storeFile.isFile) { "ANDROIDCTL_RELEASE_STORE_FILE must name an existing keystore: $storeFile" }
    }
}

tasks.matching { it.name in setOf("packageRelease", "packageReleaseBundle") }.configureEach {
    dependsOn(validateReleaseSigningInputs)
}

interface NativeTools {
    @get:Inject
    val execOperations: ExecOperations
}

val nativeTools = objects.newInstance<NativeTools>()
val minimumAndroidApi = 30
val nativeTargets = mapOf("arm64-v8a" to "aarch64-linux-android", "x86_64" to "x86_64-linux-android")
val ndkRevision = providers.gradleProperty("androidctlNdkVersion").get()
val javaTarget = JavaVersion.VERSION_11
val nativeLibraries = objects.directoryProperty()
val compileNative by tasks.registering {
    val ndk = androidComponents.sdkComponents.ndkDirectory
    inputs.dir("src/main/c")
    inputs.property("ndkRoot", ndk.map { it.asFile.absolutePath })
    inputs.property("minimumAndroidApi", minimumAndroidApi)
    inputs.property("nativeTargets", nativeTargets)
    outputs.dir(nativeLibraries)
    doLast {
        val root = ndk.get().asFile
        val osName = System.getProperty("os.name")
        val windows = osName.startsWith("Windows")
        val platform =
            if (windows) {
                "windows-x86_64"
            } else if (osName == "Mac OS X") {
                "darwin-x86_64"
            } else {
                "linux-x86_64"
            }
        val toolchain = root.resolve("toolchains/llvm/prebuilt/$platform")
        val clang = toolchain.resolve("bin/clang${if (windows) ".exe" else ""}")
        for ((abi, target) in nativeTargets) {
            val destination =
                nativeLibraries
                    .get()
                    .dir(abi)
                    .asFile
                    .apply { mkdirs() }

            fun compile(
                source: String,
                output: String,
                flags: List<String>,
            ) {
                nativeTools.execOperations.exec {
                    commandLine(
                        listOf(
                            clang.absolutePath,
                            "--target=$target$minimumAndroidApi",
                            "--sysroot=${toolchain.resolve("sysroot")}",
                            "-std=c17",
                            "-O2",
                            "-Wall",
                            "-Wextra",
                            "-Werror",
                            "-fstack-protector-strong",
                            "-D_FORTIFY_SOURCE=2",
                            "-Wl,-z,relro,-z,now",
                            "-Wl,-z,max-page-size=16384",
                            "-Wl,-z,common-page-size=16384",
                        ) + flags + listOf(file("src/main/c/$source").absolutePath, "-o", destination.resolve(output).absolutePath),
                    )
                }
            }
            // NativeFiles loads this JNI shared library with System.loadLibrary("androidctl_files").
            compile("files.c", "libandroidctl_files.so", listOf("-fPIC", "-shared"))
        }
    }
}

android {
    namespace = "com.rainng.androidctl"
    ndkVersion = ndkRevision
    compileSdk {
        version = release(36)
    }

    signingConfigs {
        create("release") {
            if (releaseSigningInputs.values.all(String::isNotBlank)) {
                storeFile = file(releaseSigningInputs.getValue("ANDROIDCTL_RELEASE_STORE_FILE"))
                storePassword = releaseSigningInputs.getValue("ANDROIDCTL_RELEASE_STORE_PASSWORD")
                keyAlias = releaseSigningInputs.getValue("ANDROIDCTL_RELEASE_KEY_ALIAS")
                keyPassword = releaseSigningInputs.getValue("ANDROIDCTL_RELEASE_KEY_PASSWORD")
            }
        }
    }

    defaultConfig {
        ndk.abiFilters.addAll(nativeTargets.keys)
        applicationId = "com.rainng.androidctl"
        minSdk = minimumAndroidApi
        targetSdk = 36
        versionCode = canonicalRelease.code
        versionName = canonicalRelease.name
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = javaTarget
        targetCompatibility = javaTarget
    }
    buildFeatures {
        buildConfig = true
        compose = true
        aidl = true
    }
    sourceSets.getByName("test").resources.srcDir(rootProject.file("../contract"))
    // Extract native artifacts for the worker.
    packaging.jniLibs.useLegacyPackaging = true
}

androidComponents {
    onVariants { variant ->
        variant.sources.jniLibs?.addGeneratedSourceDirectory(compileNative) { nativeLibraries }
    }
    beforeVariants(selector().all()) { variantBuilder ->
        variantBuilder.enableAndroidTest = false
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.fromTarget(javaTarget.toString()))
    }
}

detekt {
    buildUponDefaultConfig = true
    parallel = true
    config.setFrom("$rootDir/detekt.yml")
    basePath = rootDir.absolutePath
}

tasks.withType<Detekt>().configureEach {
    jvmTarget = javaTarget.toString()
    reports {
        html.required.set(true)
        sarif.required.set(false)
        txt.required.set(false)
        xml.required.set(false)
    }
}

val websocketUpstream by configurations.creating
val websocketVersion = libs.versions.websocket.get()
val compileWebsocketFix by tasks.registering(JavaCompile::class) {
    source(rootProject.fileTree("third-party/java-websocket/src"))
    classpath = websocketUpstream
    destinationDirectory.set(layout.buildDirectory.dir("websocket-fix-classes"))
    sourceCompatibility = javaTarget.toString()
    targetCompatibility = javaTarget.toString()
}
val patchedWebsocket by tasks.registering(Jar::class) {
    dependsOn(compileWebsocketFix)
    inputs.files(websocketUpstream)
    from(provider { zipTree(websocketUpstream.files.single { it.name == "Java-WebSocket-$websocketVersion.jar" }) }) {
        exclude("org/java_websocket/SocketChannelIOHelper.class", "org/java_websocket/SSLSocketChannel2.class", "META-INF/MANIFEST.MF")
    }
    from(compileWebsocketFix.flatMap { it.destinationDirectory })
    archiveFileName.set("Java-WebSocket-$websocketVersion-androidctl.jar")
    destinationDirectory.set(layout.buildDirectory.dir("websocket-fix-jar"))
}
dependencies {
    websocketUpstream(libs.websocket)
    implementation(files(patchedWebsocket))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.material3)
    implementation(libs.shizuku.api)
    implementation(libs.shizuku.provider)
    // The patched files JAR has no POM to bring its SLF4J dependency.
    implementation(libs.slf4j)
    implementation(libs.bcprov)
    implementation(libs.bcutil)
    implementation(libs.bcpkix)
    testImplementation(libs.json)
    testImplementation(libs.junit)
}

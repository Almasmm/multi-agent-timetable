// CI/CD pipeline of the multi-agent timetable generator (declarative Jenkins pipeline).
//
// Every branch, every pushed commit (multibranch scan, see ci/jenkins/casc.yaml):
//   Prepare -> Build test image -> Lint -> Tests (3 suites in parallel, JUnit + coverage)
//   -> Build runtime image -> Smoke test (container health + API + a real timetable run)
// main only (CD):
//   -> Push to Docker Hub (when DOCKERHUB_REPO is configured) -> Deploy to Minikube (rollout + health)
//
// All heavy work happens in containers built from the repository Dockerfile, so CI uses exactly the same
// Python, OR-Tools and libraries as the image that is shipped.
pipeline {
    agent any

    options {
        timeout(time: 40, unit: 'MINUTES')
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    environment {
        IMAGE_NAME = 'multi-agent-timetable'
        DOCKER_BUILDKIT = '1'
        TT_SOLVER_WORKERS = '4'
    }

    stages {
        stage('Prepare') {
            steps {
                script {
                    env.SHORT_SHA = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
                    env.IMAGE_TAG = "${env.BRANCH_NAME.replaceAll('[^A-Za-z0-9_.-]', '-')}-${env.SHORT_SHA}"
                    env.TEST_IMAGE = "${env.IMAGE_NAME}:test-${env.SHORT_SHA}"
                    env.APP_IMAGE = "${env.IMAGE_NAME}:${env.IMAGE_TAG}"
                    currentBuild.description = env.APP_IMAGE
                }
                sh '''
                    git log -1 --format='Commit %h by %an: %s'
                    docker version --format 'Docker engine {{.Server.Version}}'
                    rm -rf reports build && mkdir -p reports
                '''
            }
        }

        stage('Build test image') {
            steps {
                sh 'docker build --target test --tag "$TEST_IMAGE" .'
            }
        }

        stage('Lint') {
            steps {
                sh 'docker run --rm "$TEST_IMAGE" ruff check timetable tests'
            }
        }

        stage('Tests') {
            parallel {
                stage('Unit + service') {
                    steps {
                        sh 'bash ci/scripts/run-tests.sh "$TEST_IMAGE" units tests/test_units.py tests/test_service.py'
                    }
                }
                stage('Web API') {
                    steps {
                        sh 'bash ci/scripts/run-tests.sh "$TEST_IMAGE" web tests/test_web.py'
                    }
                }
                stage('Scenarios (19)') {
                    steps {
                        sh 'bash ci/scripts/run-tests.sh "$TEST_IMAGE" scenarios tests/test_scenarios.py'
                    }
                }
            }
            post {
                always {
                    junit testResults: 'reports/junit-*.xml'
                    recordCoverage(tools: [[parser: 'COBERTURA', pattern: 'reports/coverage-*.xml']],
                                   id: 'coverage', name: 'Python coverage', sourceCodeRetention: 'NEVER')
                }
            }
        }

        stage('Build image') {
            steps {
                sh '''
                    docker build --target runtime \
                      --build-arg APP_VERSION="$IMAGE_TAG" \
                      --build-arg VCS_REF="$GIT_COMMIT" \
                      --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
                      --tag "$APP_IMAGE" --tag "$IMAGE_NAME:latest" .
                    docker image inspect "$APP_IMAGE" --format 'Built {{index .RepoTags 0}}  id={{.Id}}  size={{.Size}} bytes'
                '''
            }
        }

        stage('Smoke test') {
            steps {
                sh 'bash ci/scripts/smoke-test.sh "$APP_IMAGE"'
            }
        }

        stage('Push to Docker Hub') {
            when {
                allOf {
                    branch 'main'
                    expression { env.DOCKERHUB_REPO?.trim() }
                }
            }
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub',
                                                  usernameVariable: 'DH_USER', passwordVariable: 'DH_TOKEN')]) {
                    sh '''
                        export DOCKER_CONFIG="$(mktemp -d)"   # login lives only for this step
                        trap 'docker logout >/dev/null 2>&1; rm -rf "$DOCKER_CONFIG"' EXIT
                        echo "$DH_TOKEN" | docker login --username "$DH_USER" --password-stdin
                        for tag in "$IMAGE_TAG" latest; do
                            docker tag "$APP_IMAGE" "$DOCKERHUB_REPO:$tag"
                            docker push "$DOCKERHUB_REPO:$tag"
                        done
                    '''
                }
            }
        }

        stage('Deploy to Minikube') {
            when { branch 'main' }
            steps {
                withCredentials([file(credentialsId: 'minikube-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'bash ci/scripts/deploy-minikube.sh "$APP_IMAGE"'
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'reports/**, build/k8s/**', allowEmptyArchive: true
            sh 'docker image rm "$TEST_IMAGE" >/dev/null 2>&1 || true'
        }
        success {
            echo "Pipeline succeeded: ${env.APP_IMAGE}"
        }
        failure {
            echo 'Pipeline failed: open the red stage in Stage View, its log shows the failing command.'
        }
        cleanup {
            cleanWs(deleteDirs: true, notFailBuild: true)
        }
    }
}

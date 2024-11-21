from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from elasticsearch import Elasticsearch
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from pydantic import BaseModel
import uvicorn
import os

# Enhanced Pydantic models
class Student(BaseModel):
    student_id: str
    name: str
    grade: str
    email: str

class Assignment(BaseModel):
    title: str
    description: str
    due_date: str
    subject: str
    assignment_id: Optional[str] = None
    total_points: int = 100

class AssignmentSubmission(BaseModel):
    submission_id: Optional[str] = None
    student_id: str
    assignment_id: str
    submission_date: str
    status: str  # 'submitted', 'graded', 'late', 'pending'
    grade: Optional[float] = None
    comments: Optional[str] = None

class DashboardStats(BaseModel):
    total_students: int
    total_assignments: int
    recent_submissions: List[Dict]
    upcoming_assignments: List[Dict]
    student_performance: List[Dict]
    assignment_completion_rates: List[Dict]

    class Config:
        arbitrary_types_allowed = True

class SearchQuery(BaseModel):
    query: str
    search_type: str

app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Elasticsearch configuration with simplified connection
es = Elasticsearch(
        hosts=[{
            'scheme': 'https',  # or 'https' if you're using secure connection
            'host': '192.168.0.47',
            'port': 9200
        }],
        basic_auth=("elastic", "7PCWG-S1*q*zfk4LSLtX"),  # Replace with your actual username and password
        # If using HTTPS with self-signed certificates, uncomment below
        verify_certs=False,
        # ssl_show_warn=False
    )

# Setup Elasticsearch indices
def setup_indices():
    # Student index mapping
    student_mapping = {
        "mappings": {
            "properties": {
                "name": {"type": "text"},
                "student_id": {"type": "keyword"},
                "grade": {"type": "keyword"},
                "email": {"type": "keyword"}
            }
        }
    }
    
    # Assignment index mapping
    assignment_mapping = {
        "mappings": {
            "properties": {
                "title": {"type": "text"},
                "description": {"type": "text"},
                "due_date": {"type": "date"},
                "subject": {"type": "keyword"},
                "assignment_id": {"type": "keyword"}
            }
        }
    }

    submission_mapping = {
        "mappings": {
            "properties": {
                "submission_id": {"type": "keyword"},
                "student_id": {"type": "keyword"},
                "assignment_id": {"type": "keyword"},
                "submission_date": {"type": "date"},
                "status": {"type": "keyword"},
                "grade": {"type": "float"},
                "comments": {"type": "text"}
            }
        }
    }
    
    # Create submission index if it doesn't exist
    if not es.indices.exists(index='submissions'):
        es.indices.create(index='submissions', body=submission_mapping)
    
    # Create indices if they don't exist
    if not es.indices.exists(index='students'):
        es.indices.create(index='students', body=student_mapping)
    
    if not es.indices.exists(index='assignments'):
        es.indices.create(index='assignments', body=assignment_mapping)

# Call setup_indices when the app starts
@app.on_event("startup")
async def startup_event():
    setup_indices()

@app.get("/api/dashboard", response_model=DashboardStats)
async def get_dashboard_stats():
    try:
        # Get total counts
        student_count = es.count(index="students")["count"]
        assignment_count = es.count(index="assignments")["count"]

        # Get recent submissions (last 7 days)
        recent_submissions_query = {
            "query": {
                "range": {
                    "submission_date": {
                        "gte": "now-7d/d"
                    }
                }
            },
            "sort": [{"submission_date": "desc"}],
            "size": 10
        }
        recent_submissions = es.search(index="submissions", body=recent_submissions_query)
        
        # Get upcoming assignments
        upcoming_assignments_query = {
            "query": {
                "range": {
                    "due_date": {
                        "gte": "now/d",
                        "lte": "now+14d/d"
                    }
                }
            },
            "sort": [{"due_date": "asc"}],
            "size": 5
        }
        upcoming = es.search(index="assignments", body=upcoming_assignments_query)

        # Calculate student performance
        performance_query = {
            "aggs": {
                "grade_ranges": {
                    "range": {
                        "field": "grade",
                        "ranges": [
                            {"to": 60},
                            {"from": 60, "to": 70},
                            {"from": 70, "to": 80},
                            {"from": 80, "to": 90},
                            {"from": 90}
                        ]
                    }
                }
            }
        }
        performance = es.search(index="submissions", body=performance_query)

        # Calculate assignment completion rates
        completion_query = {
            "aggs": {
                "status_counts": {
                    "terms": {
                        "field": "status"
                    }
                }
            }
        }
        completion = es.search(index="submissions", body=completion_query)

        return DashboardStats(
            total_students=student_count,
            total_assignments=assignment_count,
            recent_submissions=[hit["_source"] for hit in recent_submissions["hits"]["hits"]],
            upcoming_assignments=[hit["_source"] for hit in upcoming["hits"]["hits"]],
            student_performance=performance["aggregations"]["grade_ranges"]["buckets"],
            assignment_completion_rates=completion["aggregations"]["status_counts"]["buckets"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Assignment submission endpoints
@app.post("/api/submissions", response_model=AssignmentSubmission)
async def create_submission(submission: AssignmentSubmission):
    try:
        if not submission.submission_id:
            submission.submission_id = f"SUB{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Verify student and assignment exist
        student = es.get(index="students", id=submission.student_id)
        assignment = es.get(index="assignments", id=submission.assignment_id)
        
        result = es.index(index='submissions', id=submission.submission_id, document=submission.dict())
        if result['result'] == 'created':
            return submission
        raise HTTPException(status_code=400, detail="Failed to create submission")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/submissions/student/{student_id}")
async def get_student_submissions(student_id: str):
    try:
        query = {
            "query": {
                "match": {
                    "student_id": student_id
                }
            },
            "sort": [{"submission_date": "desc"}]
        }
        result = es.search(index="submissions", body=query)
        return {"submissions": [hit["_source"] for hit in result["hits"]["hits"]]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/submissions/assignment/{assignment_id}")
async def get_assignment_submissions(assignment_id: str):
    try:
        query = {
            "query": {
                "match": {
                    "assignment_id": assignment_id
                }
            },
            "sort": [{"submission_date": "desc"}]
        }
        result = es.search(index="submissions", body=query)
        return {"submissions": [hit["_source"] for hit in result["hits"]["hits"]]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Student performance endpoint
@app.get("/api/students/{student_id}/performance")
async def get_student_performance(student_id: str):
    try:
        # Get student's submissions
        submissions_query = {
            "query": {
                "match": {
                    "student_id": student_id
                }
            },
            "aggs": {
                "average_grade": {"avg": {"field": "grade"}},
                "grade_distribution": {
                    "terms": {
                        "field": "status"
                    }
                }
            }
        }
        submissions = es.search(index="submissions", body=submissions_query)
        
        # Get student details
        student = es.get(index="students", id=student_id)
        
        return {
            "student_info": student["_source"],
            "performance_stats": {
                "average_grade": submissions["aggregations"]["average_grade"]["value"],
                "submission_distribution": submissions["aggregations"]["grade_distribution"]["buckets"],
                "recent_submissions": [hit["_source"] for hit in submissions["hits"]["hits"][:5]]
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Assignment analytics endpoint
@app.get("/api/assignments/{assignment_id}/analytics")
async def get_assignment_analytics(assignment_id: str):
    try:
        # Get assignment submissions
        submissions_query = {
            "query": {
                "match": {
                    "assignment_id": assignment_id
                }
            },
            "aggs": {
                "average_grade": {"avg": {"field": "grade"}},
                "grade_distribution": {
                    "histogram": {
                        "field": "grade",
                        "interval": 10
                    }
                },
                "submission_status": {
                    "terms": {
                        "field": "status"
                    }
                }
            }
        }
        submissions = es.search(index="submissions", body=submissions_query)
        
        # Get assignment details
        assignment = es.get(index="assignments", id=assignment_id)
        
        return {
            "assignment_info": assignment["_source"],
            "analytics": {
                "average_grade": submissions["aggregations"]["average_grade"]["value"],
                "grade_distribution": submissions["aggregations"]["grade_distribution"]["buckets"],
                "submission_status": submissions["aggregations"]["submission_status"]["buckets"]
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Student CRUD operations
@app.post("/api/students", response_model=Student)
async def create_student(student: Student):
    try:
        result = es.index(index='students', id=student.student_id, document=student.dict())
        if result['result'] == 'created':
            return student
        raise HTTPException(status_code=400, detail="Failed to create student")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/students", response_model=List[Student])
async def get_students():
    try:
        result = es.search(index="students", body={"query": {"match_all": {}}, "size": 100})
        students = [Student(**hit['_source']) for hit in result['hits']['hits']]
        return students
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Assignment CRUD operations
@app.post("/api/assignments", response_model=Assignment)
async def create_assignment(assignment: Assignment):
    try:
        # Generate assignment_id if not provided
        if not assignment.assignment_id:
            assignment.assignment_id = f"ASN{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        result = es.index(index='assignments', id=assignment.assignment_id, document=assignment.dict())
        if result['result'] == 'created':
            return assignment
        raise HTTPException(status_code=400, detail="Failed to create assignment")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/assignments", response_model=List[Assignment])
async def get_assignments():
    try:
        result = es.search(index="assignments", body={"query": {"match_all": {}}, "size": 100})
        assignments = [Assignment(**hit['_source']) for hit in result['hits']['hits']]
        return assignments
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Search endpoint
@app.get("/api/search/{search_type}")
async def search(search_type: str, query: str):
    try:
        if search_type not in ['students', 'assignments']:
            raise HTTPException(status_code=400, detail="Invalid search type")
        
        search_query = {
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["*"]
                }
            }
        }
        
        result = es.search(index=search_type, body=search_query)
        results = [hit['_source'] for hit in result['hits']['hits']]
        return {"results": results, "search_type": search_type}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Analytics routes
@app.get("/analytics/attendance")
async def get_attendance_analytics(days: int = 30):
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Example data for testing
        sample_data = []
        for i in range(days):
            date = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
            sample_data.append({
                "date": date,
                "present": 85 + (i % 10),
                "absent": 10 - (i % 5),
                "late": 5 + (i % 3)
            })
        
        return {"attendance_data": sample_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/analytics/grades")
async def get_grade_analytics():
    try:
        # Example data for testing
        return {
            "grade_distribution": {
                "A": 30,
                "B": 45,
                "C": 15,
                "D": 8,
                "F": 2
            },
            "average_score": 85.5,
            "subject_averages": [
                {"subject": "Math", "average": 88.5},
                {"subject": "Science", "average": 84.2},
                {"subject": "English", "average": 86.7}
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/analytics/performance")
async def get_performance_analytics():
    try:
        # Example data for testing
        dates = [(datetime.now() - timedelta(days=x)).strftime("%Y-%m-%d") for x in range(12)]
        return {
            "performance_data": [
                {
                    "date": date,
                    "average": round(75 + (i % 20), 2),
                    "median": round(78 + (i % 15), 2)
                }
                for i, date in enumerate(dates)
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/analytics/top-performers")
async def get_top_performers():
    try:
        # Example data for testing
        return {
            "top_performers": [
                {"student_id": "STU001", "name": "John Doe", "average": 95.5, "trend": "up"},
                {"student_id": "STU002", "name": "Jane Smith", "average": 94.2, "trend": "stable"},
                {"student_id": "STU003", "name": "Bob Wilson", "average": 93.8, "trend": "down"},
                {"student_id": "STU004", "name": "Alice Brown", "average": 92.5, "trend": "up"},
                {"student_id": "STU005", "name": "Charlie Davis", "average": 91.3, "trend": "stable"}
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Serve static files
@app.get("/")
async def read_index():
    return FileResponse('./templates/index.html')

@app.get("/students")
async def read_students_page():
    return FileResponse('./templates/students.html')

@app.get("/assignments")
async def read_students_page():
    return FileResponse('./templates/assignments.html')

@app.get("/attendance")
async def read_attendance_page():
    return FileResponse('./templates/attendance.html')

@app.get("/analytics")
async def read_attendance_page():
    return FileResponse('./templates/analytics.html')

@app.get("/grades")
async def read_grades_page():
    return FileResponse('./templates/grades.html')



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

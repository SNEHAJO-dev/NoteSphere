name = "NoteSphere"
print("Hello from", name)

marks = [85, 90, 78, 92, 88]
total = sum(marks)
print("Total:", total)

subjects = {"Maths": 85, "Physics": 90}
for subject, mark in subjects.items():
    print(subject, "->", mark)

def greet(student):
    return "Welcome, " + student + "!"

print(greet("Rahul"))